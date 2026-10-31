import io
import logging
import re
import tempfile
import traceback
import xml.etree.ElementTree as ET
import zipfile
import base64
import binascii

import posixpath
import time

from urllib.parse import unquote

from defusedxml import ElementTree as SafeET
from lxml import etree

import ebooklib
from bs4 import BeautifulSoup, NavigableString, Tag, UnicodeDammit
from bs4.element import (
    Comment,
    Declaration,
    Doctype,
    ProcessingInstruction,
)
from html.entities import html5 as HTML5_ENTITIES

from django.contrib.auth import authenticate
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Case, F, When, Count, OuterRef, Subquery, Prefetch
from django.db.models.functions import Lower, Length
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from ebooklib import ITEM_COVER, epub
from langdetect import DetectorFactory, LangDetectException, detect
from rest_framework import generics, permissions, serializers, status, viewsets, mixins
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_extensions.cache.mixins import CacheResponseMixin
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle, AnonRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from drf_spectacular.utils import extend_schema, OpenApiParameter
from drf_spectacular.types import OpenApiTypes

from reader.exceptions import TranslationServiceError
from reader.services.ai_service import AITeacherService
from reader.services.translation_service import TranslationService
from reader.throttles import TranslationThrottle, AuthThrottle, WordAnalysisThrottle
from reader.utils.google_auth import GoogleAuthService

from .pagination import DictionaryPagination, MessagesPagination
from .filters import FlashCardFilter
from .models import (
    Book,
    Chapter,
    Conversation,
    DictionaryCategory,
    DictionaryEntry,
    FlashCard,
    Message,
    Translation,
    UserBookProgress,
    UserProfile,
)
from .serializers import (
    BookCreateUpdateSerializer,
    BookDetailSerializer,
    BookListSerializer,
    BookUploadSerializer,
    ChapterDetailSerializer,
    ChapterListSerializer,
    ConversationListSerializer,
    ConversationSerializer,
    CreateConversationSerializer,
    DictionaryCategorySerializer,
    DictionaryEntrySerializer,
    DictionaryTranslationResponseSerializer,
    FlashCardSerializer,
    GoogleAuthSerializer,
    LoginSerializer,
    MessageSerializer,
    SendMessageSerializer,
    RegisterSerializer,
    SuggestionRequestSerializer,
    SuggestionResponseSerializer,
    TranslationRequestSerializer,
    TranslationResponseSerializer,
    TranslationSerializer,
    UserBookProgressSerializer,
    UserProfileSerializer,
    UserSerializer,
    UserProfileWriteSerializer,
    WordAnalysisSerializer,
    KnownWordsQuerySerializer,
)

from .services.word_analysis.analyze_service import WordAnalysisService

from .utils.text_utils import normalize_word

logger = logging.getLogger(__name__)


class AuthViewSet(viewsets.GenericViewSet):
    """ViewSet для авторизации"""

    permission_classes = [AllowAny]
    serializer_class = GoogleAuthSerializer

    def get_throttles(self):
        if self.action in ["register", "login"]:
            return [AuthThrottle()]
        return [AnonRateThrottle()]

    @staticmethod 
    def get_tokens_for_user(user):
        refresh = RefreshToken.for_user(user)
        return {
            "refresh": str(refresh),
            "access": str(refresh.access_token),
        }
    
    @action(detail=False, methods=['post'], serializer_class=RegisterSerializer)
    def register(self, request):
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            tokens = self.get_tokens_for_user(user)
            return Response(
                {
                    "user": UserSerializer(user).data,
                    "tokens": tokens,
                    "message": "Регистрация успешна",
                },
                status=status.HTTP_201_CREATED,
            )
        return Response(
            {"errors": serializer.errors, "message": "Ошибка Регистрации"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    
    @action(detail=False, methods=['post'], serializer_class=LoginSerializer)
    def login(self, request):
        serializer = self.get_serializer(data=request.data)
        if serializer.is_valid():
            user = serializer.validated_data["user"]
            tokens = self.get_tokens_for_user(user)
            return Response(
                {
                    "user": UserSerializer(user).data,
                    "tokens": tokens,
                    "message": "Вход выполнен успешно",
                },
                status=status.HTTP_200_OK,
            )
        return Response(
            {
                "errors": serializer.errors,
                "message": "Ошибка входа",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    @action(detail=False, methods=["post"], url_path="google")
    def google_auth(self, request):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            return Response(
                {"error": "Invalid data", "details": serializer.errors},
                status=status.HTTP_400_BAD_REQUEST,
            )
        id_token = serializer.validated_data["id_token"]
        google_data = GoogleAuthService.verify_google_token(id_token)
        if not google_data:
            return Response(
                {"error": "Invalid Google token"}, status=status.HTTP_401_UNAUTHORIZED
            )
        user, error = GoogleAuthService.get_or_create_user_from_google(google_data)
        if not user:
            return Response(
                {"error": f"Failed to create user: {error}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        refresh = RefreshToken.for_user(user)
        access_token = refresh.access_token

        user_serializer = UserSerializer(user)
        return Response(
            {
                "user": user_serializer.data,
                "tokens": {
                    "access": str(access_token),
                    "refresh": str(refresh),
                },
                "message": "Authentication successful",
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="refresh")
    def refresh_token(self, request):
        serializer = TokenRefreshSerializer(data=request.data)

        try:
            serializer.is_valid(raise_exception=True)
        except Exception:
            return Response(
                {"error": "Invalid or expired refresh token"},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        return Response(serializer.validated_data, status=status.HTTP_200_OK)


class BookViewSet(viewsets.ModelViewSet):
    serializer_class = BookCreateUpdateSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_value_regex = '[0-9]+'

    def get_serializer_class(self):
        if self.action == "list":
            return BookListSerializer
        elif self.action == "retrieve":
            return BookDetailSerializer
        elif self.action == "upload_book":
            return BookUploadSerializer
        else:
            return BookCreateUpdateSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Book.objects.none()

        last_read_subquery = (
            UserBookProgress.objects
            .filter(
                user=self.request.user,
                book=OuterRef("pk"),
            )
            .values("last_read")[:1]
        )

        queryset = (
            Book.objects
            .filter(user=self.request.user)
            .annotate(
                chapter_count=Count("chapters"),
                user_last_read=Subquery(last_read_subquery),
            )
        )

        # Для деталей книги и списка глав загружаем
        # метаданные глав, но откладываем большой content.
        if self.action in ("retrieve", "chapters"):
            queryset = queryset.prefetch_related(
                Prefetch(
                    "chapters",
                    queryset=Chapter.objects.defer("content").order_by("order"),
                )
            )

        return queryset.order_by(
            F("user_last_read").desc(nulls_last=True),
            "-uploaded_at",
        )


    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def perform_destroy(self, instance):
        book_name = instance.title
        book_user = instance.user

        try:
            if instance.file and instance.file.name:
                instance.file.delete(save=False)

            if instance.cover and instance.cover.name:
                instance.cover.delete(save=False)

            instance.delete()

        except Exception:
            logger.exception(
                "Failed to delete book %s for %s",
                book_name,
                book_user,
            )
            raise

        logger.info(
            "Book %s and its stored files were deleted for %s",
            book_name,
            book_user,
        )


    @action(detail=True, methods=["get"])
    def chapters(self, request, pk=None):
        """Получить все главы книги"""
        book = self.get_object()
        chapters = book.chapters.all()
        serializer = ChapterListSerializer(chapters, many=True)
        return Response(serializer.data)

    @action(
        detail=True,
        methods=["post"],
        url_path="chapters/(?P<chapter_pk>[0-9]+)/update_total_pages",
    )
    @extend_schema(
        parameters=[
            OpenApiParameter(
                name='chapter_pk',
                type=OpenApiTypes.INT,
                location=OpenApiParameter.PATH
            )
        ]
    )
    def update_chapter_total_pages(self, request, pk=None, chapter_pk=None):
        """
        Обновляет поле total_pages для конкретной главы.
        Принимает POST-запрос с {"total_pages": <число>}.
        """
        book = self.get_object()
        try:
            chapter = book.chapters.get(pk=chapter_pk)
        except Chapter.DoesNotExist:
            return Response(
                {"error": "Глава не найдена"}, status=status.HTTP_404_NOT_FOUND
            )

        total_pages = request.data.get("total_pages")

        if total_pages is None or not isinstance(total_pages, int) or total_pages < 0:
            return Response(
                {
                    "error": "Поле 'total_pages' обязательно и должно быть положительным числом."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Обновляем только если значение изменилось
        if chapter.total_pages != total_pages:
            chapter.total_pages = total_pages
            chapter.save(update_fields=["total_pages"])

        return Response(
            {
                "status": f"Total pages for chapter {chapter.id} updated to {total_pages}"
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["get"])
    def chapter_content(self, request, pk=None):
        """Получить содержимое конкретной главы в виде массива параграфов"""
        book = self.get_object()
        chapter_order = request.query_params.get("chapter", 1)

        try:
            chapter = book.chapters.get(order=chapter_order)
            serializer = ChapterDetailSerializer(chapter)

            data = serializer.data

            original_content = data.get("content", "")

            paragraphs = [
                p.strip() for p in re.split(r"\n\s*\n", original_content) if p.strip()
            ]

            data["content"] = paragraphs
            data["book_language"] = book.language

            return Response({"chapter": data, "total_chapters": book.chapters.count()})
        except Chapter.DoesNotExist:
            return Response({"error": "Глава не найдена"}, status=404)

    @action(detail=True, methods=["post"])
    def update_progress(self, request, pk=None):
        book = self.get_object()
        serializer = UserBookProgressSerializer(
            data=request.data, context={"request": request, "book": book}
        )
        serializer.is_valid(raise_exception=True)
        chapter = serializer.validated_data["chapter"]
        last_read_page = serializer.validated_data["last_read_page"]

        UserBookProgress.objects.update_or_create(
            user=request.user,
            book=book,
            defaults={"chapter": chapter, "last_read_page": last_read_page},
        )
        return Response({"status": "Progress updated"}, status=status.HTTP_200_OK)

    # ============================================================
    # НАСТРОЙКИ ПАРСИНГА
    # ============================================================

    MIN_CHAPTER_CHARS = 3_000
    MAX_CHAPTER_CHARS = 35_000

    MAX_ARCHIVE_SINGLE_FILE = 100 * 1024 * 1024
    MAX_ARCHIVE_TOTAL_BYTES = 150 * 1024 * 1024
    MAX_ARCHIVE_ENTRIES = 5_000
    MAX_COMPRESSION_RATIO = 1_000

    EPUB_BLOCK_TAGS = frozenset({
        "p", "div", "section", "article", "aside",
        "header", "footer", "main",
        "h1", "h2", "h3", "h4", "h5", "h6",
        "li", "ul", "ol", "dl", "dt", "dd",
        "blockquote", "pre", "table", "thead", "tbody",
        "tfoot", "tr", "td", "th", "caption",
        "figure", "figcaption", "hr", "address", "center",
    })

    EPUB_SKIP_TAGS = frozenset({
        "script", "style", "head", "title",
        "meta", "link", "svg", "noscript",
    })

    FB2_LINE_TAGS = frozenset({
        "p", "subtitle", "text-author", "v", "date",
    })

    FB2_IGNORED_BODIES = frozenset({
        "notes", "comments",
    })


    # ============================================================
    # ЗАГРУЗКА КНИГИ
    # ============================================================

    @action(
        detail=False,
        methods=["post"],
        url_path="upload",
        parser_classes=[MultiPartParser],
    )
    def upload_book(self, request):
        """
        Загружает FB2, EPUB или ZIP с одной поддерживаемой книгой.

        Парсинг выполняется до создания записей в БД.
        Книга и главы записываются в одной транзакции.
        Формат существующего API не меняется.
        """
        upload_serializer = self.get_serializer(data=request.data)
        upload_serializer.is_valid(raise_exception=True)

        uploaded_file = upload_serializer.validated_data["file"]
        original_filename = uploaded_file.name

        storage_files = []

        def cleanup_storage_files():
            # Файловое хранилище не участвует в транзакции БД.
            # Поэтому удаляем уже сохранённые файлы, если запись
            # книги или глав завершилась ошибкой.
            for storage, name in reversed(storage_files):
                try:
                    if name:
                        storage.delete(name)
                except Exception:
                    logger.exception(
                        "Не удалось удалить файл после ошибки импорта: %s",
                        name,
                    )

        started_at = time.perf_counter()

        try:
            # ----------------------------------------------------
            # 1. Чтение и определение формата
            # ----------------------------------------------------

            read_started = time.perf_counter()
            uploaded_bytes = uploaded_file.read()
            read_seconds = time.perf_counter() - read_started

            extract_started = time.perf_counter()

            stored_filename, file_content, book_format = (
                self._extract_upload_payload(
                    original_filename,
                    uploaded_bytes,
                )
            )

            extract_seconds = time.perf_counter() - extract_started

            # ----------------------------------------------------
            # 2. Парсинг до начала транзакции записи
            # ----------------------------------------------------

            parse_started = time.perf_counter()

            if book_format == "FB2":
                book_data = self._parse_fb2_content(file_content)
            elif book_format == "EPUB":
                book_data = self._parse_epub_content(file_content)
            else:
                raise ValueError("Не удалось определить формат книги.")

            chapters_data = book_data.get("chapters") or []

            if not chapters_data:
                raise ValueError("В книге не удалось найти текст.")

            if book_format == "FB2":
                additional_fields = book_data.get(
                    "additional_fields", {}
                )
                authors = additional_fields.get("authors", "")
                genres = additional_fields.get("genres", "")
            else:
                authors = book_data.get("authors", "")
                genres = book_data.get("genres", "")

            parse_seconds = time.perf_counter() - parse_started

            # ----------------------------------------------------
            # 3. Создание книги и глав в одной транзакции
            # ----------------------------------------------------

            db_started = time.perf_counter()

            with transaction.atomic():
                book = Book.objects.create(
                    user=request.user,
                    title=book_data.get("title") or "Без названия",
                    description=book_data.get("description") or "",
                    book_format=book_format,
                    authors=authors,
                    genres=genres,
                    language=book_data.get("language") or "unknown",
                    file_size=len(file_content),
                )

                cover_content = book_data.get("cover_content")
                cover_filename = book_data.get("cover_filename")

                if cover_content:
                    safe_cover_name = self._safe_filename(
                        cover_filename or "cover.jpg",
                        ".jpg",
                    )

                    book.cover.save(
                        safe_cover_name,
                        ContentFile(cover_content),
                        save=False,
                    )

                    storage_files.append(
                        (book.cover.storage, book.cover.name)
                    )

                book.file.save(
                    stored_filename,
                    ContentFile(file_content),
                    save=False,
                )

                storage_files.append(
                    (book.file.storage, book.file.name)
                )

                book.save()

                if book_format == "FB2":
                    self._parse_fb2_chapters(book, chapters_data)
                else:
                    self._save_epub_chapters(book, chapters_data)

                result_serializer = BookDetailSerializer(
                    book,
                    context={"request": request},
                )
                result_data = result_serializer.data

            db_seconds = time.perf_counter() - db_started

            logger.info(
                (
                    "Book import completed: title=%r format=%s "
                    "bytes=%d chapters=%d "
                    "read=%.3fs extract=%.3fs parse=%.3fs "
                    "database=%.3fs total=%.3fs"
                ),
                book.title,
                book_format,
                len(file_content),
                len(chapters_data),
                read_seconds,
                extract_seconds,
                parse_seconds,
                db_seconds,
                time.perf_counter() - started_at,
            )

            return Response(
                {
                    "message": "Книга успешно загружена",
                    "book": result_data,
                },
                status=status.HTTP_201_CREATED,
            )

        except ValueError as exc:
            cleanup_storage_files()

            logger.warning(
                "Ошибка формата или обработки файла %r: %s",
                original_filename,
                exc,
            )

            return Response(
                {"error": f"Ошибка обработки книги: {exc}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        except Exception:
            cleanup_storage_files()

            logger.exception(
                "Непредвиденная ошибка при загрузке книги %r",
                original_filename,
            )

            return Response(
                {
                    "error": (
                        "Произошла непредвиденная ошибка "
                        "при обработке файла."
                    )
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


    def _safe_filename(self, filename, expected_extension=None):
        """Оставляет только имя файла и при необходимости исправляет расширение."""
        filename = str(filename or "book").replace("\\", "/")
        filename = filename.split("/")[-1].strip()

        if not filename or filename in {".", ".."}:
            filename = "book"

        if expected_extension:
            stem = filename.rsplit(".", 1)[0] if "." in filename else filename
            filename = f"{stem}{expected_extension}"

        return filename


    def _check_zip_limits(self, archive, archive_label="архив"):
        """Проверяет распакованный размер, количество файлов и степень сжатия."""
        infos = [
            info
            for info in archive.infolist()
            if not info.is_dir()
        ]

        if len(infos) > self.MAX_ARCHIVE_ENTRIES:
            raise ValueError(
                f"{archive_label}: слишком много файлов в архиве."
            )

        total_unpacked = 0

        for info in infos:
            if info.file_size > self.MAX_ARCHIVE_SINGLE_FILE:
                raise ValueError(
                    f"{archive_label}: один из файлов слишком большой."
                )

            total_unpacked += info.file_size

            if total_unpacked > self.MAX_ARCHIVE_TOTAL_BYTES:
                raise ValueError(
                    f"{archive_label}: превышен лимит распакованного размера."
                )

            if info.file_size and not info.compress_size:
                raise ValueError(
                    f"{archive_label}: некорректный размер сжатого файла."
                )

            if info.compress_size:
                ratio = info.file_size / info.compress_size

                if ratio > self.MAX_COMPRESSION_RATIO:
                    raise ValueError(
                        f"{archive_label}: подозрительная степень сжатия."
                    )

        return infos


    def _extract_upload_payload(self, original_filename, uploaded_bytes):
        """
        Возвращает (имя сохраняемого файла, байты книги, формат).

        ZIP должен содержать ровно один поддерживаемый файл книги.
        Извлечённый EPUB не сохраняется под расширением .zip.
        """
        lowered = original_filename.lower()

        if lowered.endswith(".fb2"):
            return (
                self._safe_filename(original_filename, ".fb2"),
                uploaded_bytes,
                "FB2",
            )

        if lowered.endswith(".epub"):
            return (
                self._safe_filename(original_filename, ".epub"),
                uploaded_bytes,
                "EPUB",
            )

        if not lowered.endswith(".zip"):
            raise ValueError("Поддерживаются только FB2, EPUB и ZIP.")

        try:
            with zipfile.ZipFile(io.BytesIO(uploaded_bytes)) as archive:
                infos = self._check_zip_limits(archive, "ZIP")

                candidates = [
                    info
                    for info in infos
                    if not info.filename.replace("\\", "/").startswith("__MACOSX/")
                    and info.filename.lower().endswith((".fb2", ".epub"))
                ]

                if not candidates:
                    raise ValueError(
                        "В ZIP не найден файл FB2 или EPUB."
                    )

                if len(candidates) > 1:
                    raise ValueError(
                        "В ZIP найдено несколько книг. "
                        "Оставь в архиве только одну книгу."
                    )

                selected = candidates[0]

                if selected.file_size > self.MAX_ARCHIVE_SINGLE_FILE:
                    raise ValueError("Файл книги внутри ZIP слишком большой.")

                with archive.open(selected) as source:
                    file_content = source.read(
                        self.MAX_ARCHIVE_SINGLE_FILE + 1
                    )

                if len(file_content) > self.MAX_ARCHIVE_SINGLE_FILE:
                    raise ValueError("Файл книги внутри ZIP слишком большой.")

                if selected.filename.lower().endswith(".fb2"):
                    book_format = "FB2"
                    extension = ".fb2"
                else:
                    book_format = "EPUB"
                    extension = ".epub"

                stored_filename = self._safe_filename(
                    selected.filename,
                    extension,
                )

                return stored_filename, file_content, book_format

        except zipfile.BadZipFile as exc:
            raise ValueError("Повреждённый ZIP-архив.") from exc


    # ============================================================
    # ОБЩИЕ ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ
    # ============================================================

    def _make_chapter_unit(
        self,
        title,
        paragraphs,
        protected=False,
        origin="technical",
    ):
        """Внутреннее представление фрагмента книги до сохранения в БД."""
        return {
            "title": (title or "").strip(),
            "paragraphs": [
                paragraph.strip()
                for paragraph in paragraphs
                if paragraph and paragraph.strip()
            ],
            "protected": bool(protected),
            "origin": origin,
        }


    def _chapter_char_count(self, paragraphs):
        return sum(len(paragraph) for paragraph in paragraphs)


    
    def _split_large_chapter(self, unit):
        """
        Равномерно разделяет большую главу по границам абзацев.

        - Определяет необходимое количество частей.
        - Старается распределить текст равномерно.
        - Не превышает MAX_CHAPTER_CHARS, если это возможно
        без разрыва отдельных абзацев.
        - Если последняя часть меньше 30% целевого размера,
        перераспределяет последние две части.
        - Не разрывает одиночные абзацы.
        """

        paragraphs = unit["paragraphs"]
        total = self._chapter_char_count(paragraphs)

        max_size = self.MAX_CHAPTER_CHARS

        if total <= max_size or len(paragraphs) < 2:
            return [unit]

        # ---------------------------------------------------------
        # 1. Определяем количество частей
        # ---------------------------------------------------------

        group_count = min(
            len(paragraphs),
            (total + max_size - 1) // max_size,
        )

        if group_count < 2:
            return [unit]

        target_size = total / group_count

        # ---------------------------------------------------------
        # 2. Создаём накопительные размеры абзацев
        # ---------------------------------------------------------

        prefix_sizes = [0]

        for paragraph in paragraphs:
            prefix_sizes.append(
                prefix_sizes[-1] + len(paragraph)
            )

        # ---------------------------------------------------------
        # 3. Равномерно распределяем абзацы
        # ---------------------------------------------------------

        groups = []
        start = 0
        paragraph_count = len(paragraphs)

        for group_index in range(group_count - 1):

            remaining_groups = (
                group_count - group_index - 1
            )

            # Обязательно оставляем хотя бы один абзац
            # для каждой оставшейся части.
            max_end = paragraph_count - remaining_groups

            candidate_ends = list(
                range(start + 1, max_end + 1)
            )

            # Если возможно, выбираем только такие границы,
            # при которых текущая часть не превышает MAX.
            feasible_ends = [
                end
                for end in candidate_ends
                if (
                    prefix_sizes[end] - prefix_sizes[start]
                    <= max_size
                )
            ]

            if feasible_ends:
                candidate_ends = feasible_ends

            # Определяем желаемый размер части с учётом
            # оставшегося текста и количества оставшихся частей.
            remaining_size = (
                prefix_sizes[-1] - prefix_sizes[start]
            )

            desired_size = (
                remaining_size / (remaining_groups + 1)
            )

            # Выбираем границу, наиболее близкую к целевому размеру.
            end = min(
                candidate_ends,
                key=lambda position: abs(
                    (prefix_sizes[position] - prefix_sizes[start])
                    - desired_size
                ),
            )

            groups.append(paragraphs[start:end])
            start = end

        # Последняя часть получает оставшиеся абзацы.
        groups.append(paragraphs[start:])

        if len(groups) <= 1:
            return [unit]

        # ---------------------------------------------------------
        # 4. Проверяем размер последней части
        # ---------------------------------------------------------

        last_size = self._chapter_char_count(groups[-1])

        min_last_size = target_size * 0.30

        if (
            len(groups) >= 2
            and last_size < min_last_size
        ):
            # Объединяем последние две части временно.
            combined = groups[-2] + groups[-1]

            combined_prefix = [0]

            for paragraph in combined:
                combined_prefix.append(
                    combined_prefix[-1] + len(paragraph)
                )

            candidate_splits = list(
                range(1, len(combined))
            )

            # Предпочитаем варианты, где обе части
            # не превышают установленный максимум.
            feasible_splits = [
                split
                for split in candidate_splits
                if (
                    combined_prefix[split] <= max_size
                    and (
                        combined_prefix[-1]
                        - combined_prefix[split]
                    ) <= max_size
                )
            ]

            if feasible_splits:
                candidate_splits = feasible_splits

            # Находим наиболее равномерную границу.
            best_split = min(
                candidate_splits,
                key=lambda split: abs(
                    combined_prefix[split]
                    - (
                        combined_prefix[-1]
                        - combined_prefix[split]
                    )
                ),
            )

            groups[-2] = combined[:best_split]
            groups[-1] = combined[best_split:]

            logger.info(
                (
                    "Rebalanced last EPUB/FB2 chapter parts: "
                    "previous=%d last=%d"
                ),
                self._chapter_char_count(groups[-2]),
                self._chapter_char_count(groups[-1]),
            )

        # ---------------------------------------------------------
        # 5. Создаём итоговые главы
        # ---------------------------------------------------------

        result = []
        base_title = unit["title"] or "Chapter"

        total_groups = len(groups)

        for index, group in enumerate(groups, 1):

            result.append(
                self._make_chapter_unit(
                    title=(
                        f"{base_title} — part "
                        f"{index}/{total_groups}"
                    ),
                    paragraphs=group,
                    protected=unit["protected"],
                    origin=unit["origin"],
                )
            )

        return result



    def _normalize_chapter_units(self, units):
        """
        Объединяет только небольшие технические фрагменты.
        Главы, заданные TOC или авторскими заголовками, защищены от склейки.
        Затем делит чрезмерно большие главы.
        """
        prepared = []

        for unit in units:
            paragraphs = [
                paragraph.strip()
                for paragraph in unit.get("paragraphs", [])
                if paragraph and paragraph.strip()
            ]

            if not paragraphs:
                continue

            current = self._make_chapter_unit(
                title=unit.get("title", ""),
                paragraphs=paragraphs,
                protected=unit.get("protected", False),
                origin=unit.get("origin", "technical"),
            )

            size = self._chapter_char_count(current["paragraphs"])

            if (
                not current["protected"]
                and size < self.MIN_CHAPTER_CHARS
                and prepared
            ):
                previous = prepared[-1]
                combined_size = (
                    self._chapter_char_count(previous["paragraphs"]) + size
                )

                if combined_size <= self.MAX_CHAPTER_CHARS:
                    previous["paragraphs"].extend(current["paragraphs"])
                    continue

            prepared.append(current)

        normalized = []

        for unit in prepared:
            normalized.extend(self._split_large_chapter(unit))

        result = []

        for index, unit in enumerate(normalized, 1):
            title = (
                unit["title"]
                or f"Chapter {index}"
            )[:500]

            result.append(
                {
                    "title": title,
                    "content": "\n\n".join(unit["paragraphs"]),
                    "order": index,
                }
            )

        if not result:
            raise ValueError("В книге не удалось найти текст.")

        return result


    # ============================================================
    # ЯЗЫК КНИГИ
    # ============================================================

    def _normalize_language_code(self, language):
        if not language:
            return ""

        code = (
            str(language)
            .strip()
            .lower()
            .replace("_", "-")
            .split("-")[0]
        )

        aliases = {
            "eng": "en",
            "spa": "es",
            "fra": "fr",
            "fre": "fr",
            "deu": "de",
            "ger": "de",
            "ukr": "uk",
            "rus": "ru",
            "por": "pt",
            "ita": "it",
            "zho": "zh",
            "chi": "zh",
            "jpn": "ja",
        }

        return aliases.get(code, code)


    def _supported_language_codes(self):
        try:
            choices = Book._meta.get_field("language").choices
            return {
                self._normalize_language_code(code)
                for code, _label in choices
                if code
            }
        except Exception:
            return set()


    def _detect_language_from_content(self, chapters_data):
        text_sample = "\n".join(
            chapter.get("content", "")
            for chapter in chapters_data[:5]
        )[:5_000]

        if not text_sample.strip():
            return None

        try:

            DetectorFactory.seed = 0
            detected = self._normalize_language_code(
                detect(text_sample)
            )

            supported = self._supported_language_codes()

            if detected and (
                not supported or detected in supported
            ):
                return detected

        except Exception:
            logger.warning(
                "Не удалось определить язык книги по тексту.",
                exc_info=True,
            )

        return None


    def _resolve_book_language(self, metadata_language, chapters_data):
        """
        Сначала валидные метаданные, затем определение по тексту.
        Не присваивает поддерживаемый языковой код только потому,
        что он является значением по умолчанию модели.
        """
        supported = self._supported_language_codes()
        metadata_code = self._normalize_language_code(metadata_language)

        if metadata_code and (
            not supported or metadata_code in supported
        ):
            return metadata_code

        detected = self._detect_language_from_content(chapters_data)

        return detected or "unknown"


    # ============================================================
    # FB2
    # ============================================================

    @staticmethod
    def _xml_local_name(element):
        tag = getattr(element, "tag", "")

        if not isinstance(tag, str):
            return ""

        return tag.rsplit("}", 1)[-1].lower()


    @staticmethod
    def _xml_text(element):
        if element is None:
            return ""

        return " ".join(
            "".join(element.itertext()).split()
        )


    def _find_fb2_child(self, element, name):
        if element is None:
            return None

        for child in element:
            if self._xml_local_name(child) == name.lower():
                return child

        return None


    def _find_fb2_descendant(self, element, name):
        if element is None:
            return None

        for child in element.iter():
            if self._xml_local_name(child) == name.lower():
                return child

        return None


    def _load_fb2_root(self, file_content):
        """
        Сначала обычный безопасный XML-разбор.
        lxml recover — только резервный путь при ошибке разбора.
        """
        try:
            try:
                return SafeET.fromstring(file_content)
            except ImportError:
                return ET.fromstring(file_content)

        except Exception as initial_error:
            logger.warning(
                "Обычный разбор FB2 не удался; пробую lxml recover: %s",
                initial_error,
            )

            try:

                parser = etree.XMLParser(
                    recover=True,
                    resolve_entities=False,
                    no_network=True,
                    huge_tree=False,
                )

                root = etree.fromstring(file_content, parser=parser)

                if root is None:
                    raise ValueError("XML-парсер не вернул корневой элемент.")

                return root

            except Exception as fallback_error:
                raise ValueError(
                    "Некорректный XML формата FB2."
                ) from fallback_error


    def _validate_fb2_structure(self, root, namespace=None):
        if self._find_fb2_descendant(root, "title-info") is None:
            raise ValueError(
                "FB2-файл повреждён: отсутствует title-info."
            )

        if self._find_fb2_descendant(root, "body") is None:
            raise ValueError(
                "FB2-файл повреждён: отсутствует body."
            )


    def _extract_fb2_metadata(self, root, namespace=None):
        title_info = self._find_fb2_descendant(root, "title-info")

        title_element = self._find_fb2_child(
            title_info,
            "book-title",
        )

        title = self._xml_text(title_element) or "Без названия"

        authors = []

        for author in title_info:
            if self._xml_local_name(author) != "author":
                continue

            first_name = self._xml_text(
                self._find_fb2_child(author, "first-name")
            )
            middle_name = self._xml_text(
                self._find_fb2_child(author, "middle-name")
            )
            last_name = self._xml_text(
                self._find_fb2_child(author, "last-name")
            )

            name = " ".join(
                part
                for part in (first_name, middle_name, last_name)
                if part
            )

            if name:
                authors.append(name)

        genres = []

        for element in title_info.iter():
            if self._xml_local_name(element) == "genre":
                genre = self._xml_text(element)

                if genre and genre not in genres:
                    genres.append(genre)

        language = self._xml_text(
            self._find_fb2_child(title_info, "lang")
        )

        annotation = self._find_fb2_descendant(
            title_info,
            "annotation",
        )

        description_paragraphs = []

        if annotation is not None:
            for element in annotation.iter():
                if self._xml_local_name(element) == "p":
                    text = self._xml_text(element)

                    if text:
                        description_paragraphs.append(text)

            if not description_paragraphs:
                text = self._xml_text(annotation)

                if text:
                    description_paragraphs.append(text)

        return {
            "title": title,
            "description": "\n\n".join(description_paragraphs),
            "language": language,
            "additional_fields": {
                "authors": ", ".join(authors),
                "genres": ", ".join(genres),
            },
        }


    def _collect_fb2_element(self, element, output):
        """
        Извлекает текстовые блоки в порядке XML-документа.

        Неизвестные контейнеры обходятся рекурсивно, поэтому содержимое,
        например, таблицы не теряется только из-за названия тега.
        """
        tag = self._xml_local_name(element)

        if tag in {"section", "binary", "image"}:
            return

        if tag in self.FB2_LINE_TAGS:
            text = self._xml_text(element)

            if text:
                output.append(text)

            return

        children = list(element)

        if not children:
            text = self._xml_text(element)

            if text:
                output.append(text)

            return

        # Текст перед первым дочерним элементом.
        direct_text = " ".join((element.text or "").split())

        if direct_text:
            output.append(direct_text)

        for child in children:
            self._collect_fb2_element(child, output)

            # В XML хвостовой текст находится после дочернего элемента.
            tail = " ".join((child.tail or "").split())

            if tail:
                output.append(tail)

    def _is_fb2_decorative_separator(self, text):
        """
        Определяет короткие декоративные разделители:
        * * *, ***, — — —, - - -, • • • и подобные.
        """
        compact = "".join((text or "").split())

        if compact == "⁂":
            return True

        separator_chars = {
            "*",
            "•",
            "·",
            "—",
            "–",
            "-",
            "_",
            "=",
            "~",
        }

        return (
            len(compact) >= 3
            and len(set(compact)) == 1
            and compact[0] in separator_chars
        )


    def _fb2_section_title(self, section):
        title_element = self._find_fb2_child(section, "title")

        if title_element is None:
            return ""

        pieces = []
        self._collect_fb2_element(title_element, pieces)

        return " ".join(" ".join(pieces).split())


    def _walk_fb2_section(self, section, units, parent_title=""):
        """
        Рекурсивно обходит FB2-секции.

        Сохраняет иерархию заголовков и текст родительских секций
        до, между и после дочерних секций.

        Декоративные разделители сохраняются в тексте, но сами по себе
        не создают защищённых глав.
        """
        raw_title = self._fb2_section_title(section)
        is_separator_title = self._is_fb2_decorative_separator(raw_title)

        # Формируем полный заголовок текущей секции.
        if raw_title and not is_separator_title:
            title = (
                f"{parent_title} — {raw_title}"
                if parent_title
                else raw_title
            )
        else:
            title = ""

        # Передаём дочерним секциям последний значимый заголовок.
        child_parent_title = title or parent_title

        buffer = []
        segment_number = 0
        saw_nested_section = False

        def flush_segment():
            nonlocal segment_number

            paragraphs = [
                text.strip()
                for text in buffer
                if text and text.strip()
            ]

            buffer.clear()

            if not paragraphs:
                return

            # Если фрагмент состоит только из декоративных разделителей,
            # он не должен становиться защищённой главой.
            has_real_text = any(
                not self._is_fb2_decorative_separator(text)
                for text in paragraphs
            )

            if title and has_real_text:
                if segment_number == 0 and not saw_nested_section:
                    segment_title = title
                else:
                    continuation_number = max(1, segment_number)

                    suffix = (
                        " (продолжение)"
                        if continuation_number == 1
                        else f" (продолжение {continuation_number})"
                    )

                    segment_title = f"{title}{suffix}"

                protected = True
                origin = "section"
            else:
                segment_title = ""
                protected = False
                origin = "technical"

            units.append(
                self._make_chapter_unit(
                    segment_title,
                    paragraphs,
                    protected=protected,
                    origin=origin,
                )
            )

            segment_number += 1

        for child in section:
            tag = self._xml_local_name(child)

            if tag == "title":
                # Обычный заголовок уже используется как название секции.
                # Но декоративный заголовок сохраняем как текстовый фрагмент.
                if is_separator_title and raw_title:
                    buffer.append(raw_title)

            elif tag == "section":
                flush_segment()

                self._walk_fb2_section(
                    child,
                    units,
                    parent_title=child_parent_title,
                )

                saw_nested_section = True

            else:
                self._collect_fb2_element(child, buffer)

            # Обрабатываем tail и для title, чтобы не пропускать текст
            # между соседними XML-элементами.
            tail = " ".join((child.tail or "").split())

            if tail:
                buffer.append(tail)

        flush_segment()


    def _extract_fb2_chapters(self, root, namespace=None):
        units = []

        for body in root.iter():
            if self._xml_local_name(body) != "body":
                continue

            if (body.get("name") or "").lower() in self.FB2_IGNORED_BODIES:
                continue

            children = list(body)

            has_sections = any(
                self._xml_local_name(child) == "section"
                for child in children
            )

            raw_body_title = (
                self._fb2_section_title(body)
                if has_sections
                else ""
            )

            body_title_is_separator = self._is_fb2_decorative_separator(
                raw_body_title
            )

            body_title = (
                ""
                if body_title_is_separator
                else raw_body_title
            )

            buffer = []

            def flush_body_text():
                paragraphs = [
                    text.strip()
                    for text in buffer
                    if text and text.strip()
                ]

                buffer.clear()

                if paragraphs:
                    units.append(
                        self._make_chapter_unit(
                            "",
                            paragraphs,
                            protected=False,
                            origin="technical",
                        )
                    )

            for child in children:
                tag = self._xml_local_name(child)

                if tag == "title" and has_sections:
                    # Заголовок body передаётся дочерним секциям.
                    # Декоративный заголовок при этом сохраняем как текст.
                    if body_title_is_separator and raw_body_title:
                        buffer.append(raw_body_title)

                elif tag == "section":
                    flush_body_text()

                    self._walk_fb2_section(
                        child,
                        units,
                        parent_title=body_title,
                    )

                else:
                    self._collect_fb2_element(child, buffer)

                tail = " ".join((child.tail or "").split())

                if tail:
                    buffer.append(tail)

            flush_body_text()

        return self._normalize_chapter_units(units)


    def _extract_fb2_cover(self, root, namespace=None, title_info=None):
        if title_info is None:
            title_info = self._find_fb2_descendant(root, "title-info")

        if title_info is None:
            return None, None

        coverpage = self._find_fb2_child(title_info, "coverpage")

        if coverpage is None:
            return None, None

        image_element = self._find_fb2_descendant(coverpage, "image")

        if image_element is None:
            return None, None

        href = None

        for attr_name, attr_value in image_element.attrib.items():
            if attr_name.rsplit("}", 1)[-1].lower() == "href":
                href = attr_value
                break

        if not href:
            return None, None

        cover_id = href.lstrip("#").strip()

        for element in root.iter():
            if self._xml_local_name(element) != "binary":
                continue

            if element.get("id") != cover_id or not element.text:
                continue

            try:
                encoded = re.sub(r"\s+", "", element.text)
                image_bytes = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                logger.warning(
                    "Не удалось декодировать обложку FB2: %s",
                    cover_id,
                )
                return None, None

            content_type = (
                element.get("content-type") or "image/jpeg"
            ).lower()

            extensions = {
                "image/jpeg": ".jpg",
                "image/jpg": ".jpg",
                "image/png": ".png",
                "image/gif": ".gif",
                "image/webp": ".webp",
            }

            extension = extensions.get(content_type, ".jpg")
            filename = self._safe_filename(cover_id, extension)

            return image_bytes, filename

        return None, None


    def _parse_fb2_content(self, file_content):
        try:
            root = self._load_fb2_root(file_content)

            self._validate_fb2_structure(root)

            book_data = self._extract_fb2_metadata(root)

            chapters = self._extract_fb2_chapters(root)

            title_info = self._find_fb2_descendant(root, "title-info")

            cover_content, cover_filename = self._extract_fb2_cover(
                root,
                title_info=title_info,
            )

            book_data["chapters"] = chapters
            book_data["cover_content"] = cover_content
            book_data["cover_filename"] = cover_filename

            book_data["language"] = self._resolve_book_language(
                book_data.get("language"),
                chapters,
            )

            return book_data

        except ValueError:
            raise

        except Exception as exc:
            logger.exception("Ошибка парсинга FB2")

            raise ValueError(
                f"Не удалось обработать FB2: {exc}"
            ) from exc


    def _parse_fb2_chapters(self, book, chapters_data):
        """Сохраняет все главы FB2 одним массовым запросом."""
        ChapterModel = book.chapters.model

        objects = [
            ChapterModel(
                book=book,
                title=chapter["title"],
                content=chapter["content"],
                order=chapter["order"],
            )
            for chapter in chapters_data
        ]

        if objects:
            ChapterModel.objects.bulk_create(
                objects,
                batch_size=500,
            )


    # ============================================================
    # EPUB: ОБХОД HTML И ОГЛАВЛЕНИЯ
    # ============================================================

    def _validate_epub_archive(self, file_content):
        try:
            with zipfile.ZipFile(io.BytesIO(file_content)) as archive:
                self._check_zip_limits(archive, "EPUB")

        except zipfile.BadZipFile as exc:
            raise ValueError("EPUB содержит повреждённый ZIP-архив.") from exc


    
    def _is_epub_document(self, item):
        """
        Проверяет, является ли элемент EPUB обычным текстовым документом.

        Исключает:
        - EpubNav — навигационный XHTML-документ;
        - элементы с ITEM_NAVIGATION;
        - элементы с properties="nav".

        Оставляет обычные XHTML/HTML-документы и ITEM_DOCUMENT.
        """

        # ---------------------------------------------------------
        # 1. Проверяем класс EpubNav
        # ---------------------------------------------------------

        nav_class = getattr(epub, "EpubNav", None)

        if nav_class is not None and isinstance(item, nav_class):
            return False

        # ---------------------------------------------------------
        # 2. Проверяем тип элемента EbookLib
        # ---------------------------------------------------------

        try:
            item_type = item.get_type()
        except Exception:
            item_type = None

        navigation_type = getattr(
            ebooklib,
            "ITEM_NAVIGATION",
            None,
        )

        if (
            navigation_type is not None
            and item_type == navigation_type
        ):
            return False

        # ---------------------------------------------------------
        # 3. Проверяем properties из manifest EPUB
        # ---------------------------------------------------------

        properties = getattr(item, "properties", None) or []

        if isinstance(properties, str):
            properties = properties.split()

        try:
            normalized_properties = {
                str(value).lower()
                for value in properties
            }
        except TypeError:
            normalized_properties = set()

        if "nav" in normalized_properties:
            return False

        # ---------------------------------------------------------
        # 4. Проверяем MIME-тип
        # ---------------------------------------------------------

        media_type = (
            getattr(item, "media_type", "") or ""
        ).split(";", 1)[0].strip().lower()

        html_types = {
            "application/xhtml+xml",
            "text/html",
            "application/x-dtbook+xml",
        }

        if media_type in html_types:
            return True

        # ---------------------------------------------------------
        # 5. Последняя проверка — тип документа
        # ---------------------------------------------------------

        return item_type == ebooklib.ITEM_DOCUMENT


    
    
    def _extract_epub_document(self, content):
        """
        Извлекает абзацы, позиции якорей и позиции заголовков из XHTML.

        Перед XML-разбором:
        - определяет кодировку;
        - нормализует HTML5 named entities;
        - сохраняет пять стандартных XML-сущностей.

        При извлечении:
        - одиночный <br> сохраняется как перенос внутри абзаца;
        - два и более последовательных <br> становятся границей абзацев;
        - вложенные block-элементы не дублируют текст.
        """

        # ---------------------------------------------------------
        # 1. Декодирование исходного содержимого
        # ---------------------------------------------------------

        if isinstance(content, bytes):
            markup = content
        elif isinstance(content, (bytearray, memoryview)):
            markup = bytes(content)
        elif isinstance(content, str):
            markup = content
        else:
            raise ValueError(
                "Неподдерживаемый тип содержимого XHTML."
            )

        decoder = UnicodeDammit(
            markup,
            is_html=False,
        )

        decoded_content = decoder.unicode_markup

        if decoded_content is None:
            raise ValueError(
                "Не удалось определить кодировку XHTML."
            )

        # ---------------------------------------------------------
        # 2. Нормализация именованных HTML5-сущностей
        # ---------------------------------------------------------

        xml_predefined_entities = {
            "amp",
            "lt",
            "gt",
            "quot",
            "apos",
        }

        entity_pattern = re.compile(
            r"&([A-Za-z][A-Za-z0-9]*);"
        )

        def replace_html_entity(match):
            entity_name = match.group(1)

            if entity_name in xml_predefined_entities:
                return match.group(0)

            replacement = HTML5_ENTITIES.get(
                f"{entity_name};"
            )

            if replacement is None:
                return match.group(0)

            return replacement

        normalized_content = entity_pattern.sub(
            replace_html_entity,
            decoded_content,
        )

        # ---------------------------------------------------------
        # 3. Разбор XHTML
        # ---------------------------------------------------------

        soup = None
        body = None

        for parser in ("xml", "lxml"):
            try:
                candidate = BeautifulSoup(
                    normalized_content,
                    parser,
                )

                candidate_body = candidate.find("body")

                if candidate_body is not None:
                    soup = candidate
                    body = candidate_body
                    break

                if soup is None:
                    soup = candidate

            except Exception:
                logger.warning(
                    "Не удалось разобрать XHTML с помощью %s",
                    parser,
                    exc_info=True,
                )

        if body is None:
            if soup is None:
                raise ValueError(
                    "Не удалось прочитать HTML-содержимое EPUB."
                )

            body = soup

        # ---------------------------------------------------------
        # 4. Подготовка структуры документа
        # ---------------------------------------------------------

        paragraphs = []
        anchors = {}
        headings = {}
        buffer = []

        # Специальный внутренний маркер настоящего HTML-тега <br>.
        # Он позволяет не путать <br> с обычными переносами
        # строк в исходном XHTML.
        BR_MARKER = "\ue000"

        def flush():
            if not buffer:
                return

            raw = "".join(buffer)
            buffer.clear()

            # Разделяем содержимое только по двум или более
            # последовательным тегам <br>.
            #
            # Пробелы и переносы между тегами допускаются:
            # <br><br>
            # <br />
            # <br />
            #
            # Одиночный <br> останется переносом внутри абзаца.

            br_separator = re.compile(
                rf"{re.escape(BR_MARKER)}"
                rf"(?:\s*{re.escape(BR_MARKER)})+"
            )

            parts = br_separator.split(raw)

            for part in parts:
                if not part.strip():
                    continue

                # Одиночный <br> превращаем в перенос внутри абзаца.
                part = part.replace(BR_MARKER, "\n")

                lines = part.splitlines() or [part]

                text = "\n".join(
                    " ".join(line.split())
                    for line in lines
                ).strip()

                if text:
                    paragraphs.append(text)

        def register_block_anchors(node):
            for key in ("id", "name"):
                value = node.get(key)

                if isinstance(value, str) and value.strip():
                    anchors.setdefault(
                        value.strip(),
                        len(paragraphs),
                    )

        # ---------------------------------------------------------
        # 5. Рекурсивный обход HTML-дерева
        # ---------------------------------------------------------

        def walk(node):
            if isinstance(node, NavigableString):
                if not isinstance(
                    node,
                    (
                        Comment,
                        Declaration,
                        Doctype,
                        ProcessingInstruction,
                    ),
                ):
                    buffer.append(str(node))

                return

            if not isinstance(node, Tag):
                return

            tag_name = (node.name or "").lower()

            if tag_name in self.EPUB_SKIP_TAGS:
                return

            if tag_name == "br":
                # Не добавляем обычный перенос напрямую.
                # Сохраняем отдельный маркер, чтобы flush()
                # мог отличить один <br> от двух последовательных.
                buffer.append(BR_MARKER)
                return

            is_block = tag_name in self.EPUB_BLOCK_TAGS

            if is_block:
                flush()
                register_block_anchors(node)

                heading_level = None

                if (
                    len(tag_name) == 2
                    and tag_name[0] == "h"
                    and tag_name[1].isdigit()
                ):
                    heading_level = int(tag_name[1])

                start_index = len(paragraphs)

                for child in node.children:
                    walk(child)

                flush()

                if (
                    heading_level is not None
                    and start_index < len(paragraphs)
                ):
                    heading_text = (
                        paragraphs[start_index]
                        .replace("\n", " ")
                        .strip()
                    )

                    if heading_text:
                        headings[start_index] = {
                            "title": heading_text,
                            "level": heading_level,
                        }

                return

            # Встроенные элементы: i, b, span, a и другие.
            # Не создаём искусственных пробелов вокруг их текста.
            for key in ("id", "name"):
                value = node.get(key)

                if not isinstance(value, str) or not value.strip():
                    continue

                pending_text = "".join(buffer)

                position = len(paragraphs)

                if pending_text.strip():
                    position += 1

                anchors.setdefault(value.strip(), position)

            for child in node.children:
                walk(child)

        for child in body.children:
            walk(child)

        flush()

        return {
            "paragraphs": paragraphs,
            "anchors": anchors,
            "headings": headings,
        }


    def _flatten_epub_toc(self, toc, output):
        """Преобразует вложенный TOC EbookLib в последовательный список."""
        for entry in toc or []:
            if isinstance(entry, (list, tuple)):
                if not entry:
                    continue

                head = entry[0]
                children = entry[1] if len(entry) > 1 else []

                self._append_epub_toc_node(head, output)
                self._flatten_epub_toc(children, output)

            else:
                self._append_epub_toc_node(entry, output)


    def _append_epub_toc_node(self, node, output):
        href = getattr(node, "href", None)

        if isinstance(node, str):
            href = node

        if not href:
            return

        title = getattr(node, "title", "") or ""

        if isinstance(title, (bytes, bytearray)):
            title = title.decode("utf-8", errors="replace")

        title = BeautifulSoup(
            str(title),
            "html.parser",
        ).get_text(" ", strip=True)

        output.append((str(href), title))

    
    def _resolve_epub_document(self, href, doc_info, by_basename):
        path = unquote(
            href.split("#", 1)[0].split("?", 1)[0]
        ).replace("\\", "/")

        if not path:
            return None

        normalized = posixpath.normpath(path).lstrip("/")

        while normalized.startswith("./"):
            normalized = normalized[2:]

        if normalized in doc_info:
            return normalized

        # Сопоставляем нормализованные пути.
        for name in doc_info:
            candidate = posixpath.normpath(name).lstrip("/")

            while candidate.startswith("./"):
                candidate = candidate[2:]

            if candidate == normalized:
                return name

        # Последний вариант — уникальное совпадение имени файла.
        matches = by_basename.get(
            posixpath.basename(normalized).lower(),
            [],
        )

        return matches[0] if len(matches) == 1 else None


    
    def _split_large_epub_unit_by_headings(
        self,
        unit,
        start,
        end,
        all_paragraphs,
        global_headings,
        force=False,
    ):
        """
        Разделяет EPUB-главу по h1/h2 только тогда,
        когда большинство получившихся фрагментов имеет
        достаточный размер.

        Маленькие фрагменты объединяются с соседними.
        Если разделение создаёт слишком много маленьких глав,
        возвращается исходная глава для обычной нормализации.

        force=True позволяет использовать алгоритм в fallback,
        даже если вся книга меньше MAX_CHAPTER_CHARS.
        """

        total = self._chapter_char_count(unit["paragraphs"])

        if total <= self.MAX_CHAPTER_CHARS and not force:
            return [unit]

        # ---------------------------------------------------------
        # 1. Находим позиции крупных заголовков h1/h2
        # ---------------------------------------------------------

        heading_positions = sorted({
            position
            for position, heading in global_headings.items()
            if (
                start < position < end
                and heading["level"] <= 2
                and heading["title"]
            )
        })

        if not heading_positions:
            return [unit]

        boundaries = [start] + heading_positions + [end]

        # ---------------------------------------------------------
        # 2. Формируем предварительные фрагменты
        # ---------------------------------------------------------

        segments = []

        for index in range(len(boundaries) - 1):
            left = boundaries[index]
            right = boundaries[index + 1]

            paragraphs = list(all_paragraphs[left:right])

            if not paragraphs:
                continue

            heading = global_headings.get(left, {})

            if index == 0:
                title = unit["title"] or heading.get("title", "")
            else:
                title = heading.get("title", "")

            segments.append({
                "title": title,
                "paragraphs": paragraphs,
                "size": self._chapter_char_count(paragraphs),
                "origin": (
                    unit.get("origin", "technical")
                    if index == 0
                    else "heading"
                ),
            })

        if len(segments) < 2:
            return [unit]

        # ---------------------------------------------------------
        # 3. Проверяем качество предполагаемого разделения
        # ---------------------------------------------------------

        reasonable_segments = sum(
            segment["size"] >= self.MIN_CHAPTER_CHARS
            for segment in segments
        )

        # Требуем строгое большинство фрагментов
        # размером не менее MIN_CHAPTER_CHARS.
        if reasonable_segments * 2 <= len(segments):
            logger.info(
                (
                    "EPUB heading split skipped: "
                    "reasonable=%d total=%d chapter_title=%r"
                ),
                reasonable_segments,
                len(segments),
                unit.get("title", ""),
            )

            return [unit]

        # ---------------------------------------------------------
        # 4. Вспомогательное объединение соседних фрагментов
        # ---------------------------------------------------------

        def merge_segments(first, second):
            paragraphs = (
                first["paragraphs"] + second["paragraphs"]
            )

            return {
                "title": first["title"] or second["title"],
                "paragraphs": paragraphs,
                "size": self._chapter_char_count(paragraphs),
                "origin": first["origin"],
            }

        # ---------------------------------------------------------
        # 5. Объединяем слишком маленькие фрагменты
        # ---------------------------------------------------------

        merged_segments = []
        pending = None

        for segment in segments:

            if segment["size"] >= self.MIN_CHAPTER_CHARS:

                if pending is not None:

                    if (
                        merged_segments
                        and (
                            merged_segments[-1]["size"] + pending["size"]
                            <= self.MAX_CHAPTER_CHARS
                        )
                    ):
                        # Сначала пытаемся присоединить маленький
                        # фрагмент к предыдущему.
                        merged_segments[-1] = merge_segments(
                            merged_segments[-1],
                            pending,
                        )

                    else:
                        # Если предыдущая глава уже достаточно большая,
                        # присоединяем маленький фрагмент к следующей.
                        segment = merge_segments(
                            pending,
                            segment,
                        )

                    pending = None

                merged_segments.append(segment)

            else:

                if (
                    merged_segments
                    and (
                        merged_segments[-1]["size"] + segment["size"]
                        <= self.MAX_CHAPTER_CHARS
                    )
                ):
                    # Маленькая подглава присоединяется к предыдущей.
                    merged_segments[-1] = merge_segments(
                        merged_segments[-1],
                        segment,
                    )

                elif pending is None:
                    pending = segment

                else:
                    # Накапливаем соседние маленькие фрагменты,
                    # чтобы не создавать множество коротких глав.
                    pending = merge_segments(
                        pending,
                        segment,
                    )

        # Обрабатываем оставшийся фрагмент после последней границы.
        if pending is not None:

            if merged_segments:
                merged_segments[-1] = merge_segments(
                    merged_segments[-1],
                    pending,
                )
            else:
                merged_segments.append(pending)

        # ---------------------------------------------------------
        # 6. Формируем итоговые главы
        # ---------------------------------------------------------

        result = []

        for segment in merged_segments:

            size = segment["size"]

            result.append(
                self._make_chapter_unit(
                    title=segment["title"],
                    paragraphs=segment["paragraphs"],
                    protected=size >= self.MIN_CHAPTER_CHARS,
                    origin=segment["origin"],
                )
            )

        return result or [unit]



    
    def _build_epub_units(
        self,
        all_paragraphs,
        document_order,
        doc_info,
        global_headings,
        toc_entries,
    ):
        """
        Формирует главы EPUB на основе TOC и заголовков h1/h2.

        Если TOC доступен:
            - использует его границы;
            - дополнительно делит большие главы по внутренним h1/h2.

        Если TOC отсутствует или не удалось разрешить его ссылки:
            - использует h1/h2;
            - применяет те же проверки размеров;
            - при неудачном разделении оставляет обычную нормализацию.

        Если заголовки отсутствуют:
            - сохраняет документы в порядке spine.
        """

        # ---------------------------------------------------------
        # 1. Индекс документов по имени файла
        # ---------------------------------------------------------

        by_basename = {}

        for name in doc_info:
            by_basename.setdefault(
                posixpath.basename(name).lower(),
                [],
            ).append(name)

        # ---------------------------------------------------------
        # 2. Определяем позиции глав из TOC
        # ---------------------------------------------------------

        starts = []

        for index, (href, title) in enumerate(toc_entries):

            name = self._resolve_epub_document(
                href,
                doc_info,
                by_basename,
            )

            if name is None:
                logger.warning(
                    "TOC EPUB ссылается на неизвестный файл: %s",
                    href,
                )
                continue

            offset = doc_info[name]["offset"]

            local_anchors = (
                doc_info[name]["extracted"]["anchors"]
            )

            anchor = (
                unquote(
                    href.split("#", 1)[1].split("?", 1)[0]
                )
                if "#" in href
                else ""
            )

            position = offset + local_anchors.get(anchor, 0)

            starts.append((position, index, title))

        starts.sort(key=lambda item: (item[0], item[1]))

        # ---------------------------------------------------------
        # 3. Удаляем повторяющиеся позиции TOC
        # ---------------------------------------------------------

        deduplicated = []

        for position, _index, title in starts:

            if not 0 <= position < len(all_paragraphs):
                continue

            if (
                deduplicated
                and deduplicated[-1][0] == position
            ):
                deduplicated[-1] = (position, title)

            else:
                deduplicated.append((position, title))

        units = []

        # ---------------------------------------------------------
        # 4. Основной вариант: используем TOC
        # ---------------------------------------------------------

        if deduplicated:

            first_position = deduplicated[0][0]

            if first_position > 0:
                units.append(
                    self._make_chapter_unit(
                        "",
                        all_paragraphs[:first_position],
                        protected=False,
                        origin="technical",
                    )
                )

            for index, (start, title) in enumerate(deduplicated):

                end = (
                    deduplicated[index + 1][0]
                    if index + 1 < len(deduplicated)
                    else len(all_paragraphs)
                )

                if end <= start:
                    continue

                unit = self._make_chapter_unit(
                    title,
                    all_paragraphs[start:end],
                    protected=True,
                    origin="toc",
                )

                # Внутренние h1/h2 разделяют только большие главы
                # и только если получаются достаточно крупные фрагменты.
                units.extend(
                    self._split_large_epub_unit_by_headings(
                        unit,
                        start,
                        end,
                        all_paragraphs,
                        global_headings,
                    )
                )

            return units

        # ---------------------------------------------------------
        # 5. Fallback: TOC отсутствует или не удалось его разрешить
        # ---------------------------------------------------------

        major_headings = [
            (position, heading)
            for position, heading in sorted(
                global_headings.items()
            )
            if (
                heading["level"] <= 2
                and heading["title"]
            )
        ]

        if major_headings:

            # Если первый документ начинается с заголовка,
            # используем его как название первой главы.
            first_heading = global_headings.get(0, {})

            fallback_unit = self._make_chapter_unit(
                title=first_heading.get("title", ""),
                paragraphs=all_paragraphs,
                protected=False,
                origin="technical",
            )

            # В fallback применяем те же правила проверки размеров,
            # даже если вся книга меньше MAX_CHAPTER_CHARS.
            return self._split_large_epub_unit_by_headings(
                fallback_unit,
                0,
                len(all_paragraphs),
                all_paragraphs,
                global_headings,
                force=True,
            )

        # ---------------------------------------------------------
        # 6. Последний вариант: сохраняем документы по spine
        # ---------------------------------------------------------

        for name in document_order:

            extracted = doc_info[name]["extracted"]

            units.append(
                self._make_chapter_unit(
                    "",
                    extracted["paragraphs"],
                    protected=False,
                    origin="technical",
                )
            )

        return units



    def _extract_epub_cover(self, book_epub):
        """Извлекает обложку EPUB2/EPUB3, если она присутствует."""
        try:
            cover_items = list(
                book_epub.get_items_of_type(ebooklib.ITEM_COVER)
            )
        except Exception:
            cover_items = []
    
        if cover_items:
            item = cover_items[0]
    
            return (
                item.get_content(),
                item.get_name().replace("\\", "/").split("/")[-1],
            )
    
        metadata = book_epub.get_metadata("OPF", "cover")
    
        if metadata:
            attrs = metadata[0][1] or {}
            cover_id = attrs.get("content")
    
            if cover_id:
                item = book_epub.get_item_with_id(cover_id)
    
                if item:
                    return (
                        item.get_content(),
                        item.get_name().replace("\\", "/").split("/")[-1],
                    )
    
        for item in book_epub.get_items_of_type(ebooklib.ITEM_IMAGE):
            if "cover" in item.get_name().lower():
                return (
                    item.get_content(),
                    item.get_name().replace("\\", "/").split("/")[-1],
                )
    
        return None, None


    def _parse_epub_content(self, file_content):
        """
        EPUB: проверка архива, чтение spine, разбор текста,
        восстановление границ TOC/якорей и нормализация размеров.
        """
        self._validate_epub_archive(file_content)

        try:
            with tempfile.NamedTemporaryFile(
                suffix=".epub",
                delete=True,
            ) as tmp:
                tmp.write(file_content)
                tmp.flush()

                book_epub = epub.read_epub(tmp.name)

                title_metadata = book_epub.get_metadata("DC", "title")
                title = (
                    title_metadata[0][0]
                    if title_metadata and title_metadata[0][0]
                    else "Без названия"
                )

                author_metadata = book_epub.get_metadata("DC", "creator")
                authors = ", ".join(
                    item[0]
                    for item in (author_metadata or [])
                    if item and item[0]
                )

                subject_metadata = book_epub.get_metadata("DC", "subject")
                genres = ", ".join(
                    item[0]
                    for item in (subject_metadata or [])
                    if item and item[0]
                )

                description_metadata = book_epub.get_metadata(
                    "DC",
                    "description",
                )

                description = ""

                if description_metadata and description_metadata[0][0]:
                    description = BeautifulSoup(
                        description_metadata[0][0],
                        "lxml",
                    ).get_text(" ", strip=True)

                language_metadata = book_epub.get_metadata(
                    "DC",
                    "language",
                )

                metadata_language = (
                    language_metadata[0][0]
                    if language_metadata
                    else None
                )

                cover_content, cover_filename = (
                    self._extract_epub_cover(book_epub)
                )

                # -----------------------------------------------
                # 1. Документы в порядке чтения из spine
                # -----------------------------------------------

                items = []
                seen_names = set()

                for entry in book_epub.spine or []:
                    if isinstance(entry, (tuple, list)):
                        idref = entry[0]
                        linear = entry[1] if len(entry) > 1 else "yes"
                    else:
                        idref = entry
                        linear = "yes"

                    if str(linear).lower() == "no":
                        continue

                    item = book_epub.get_item_with_id(idref)

                    if (
                        item is not None
                        and self._is_epub_document(item)
                        and item.get_name() not in seen_names
                    ):
                        items.append(item)
                        seen_names.add(item.get_name())

                # Если spine отсутствует или в нём не все документы,
                # добавляем оставшиеся текстовые документы в порядке
                # их появления в manifest.
                # Резервный обход manifest используем только тогда,
                # когда EPUB вообще не содержит spine.
                # Если spine существует, соблюдаем его порядок и linear="no".
                if not (book_epub.spine or []):
                    for item in book_epub.get_items():
                        name = item.get_name()

                        if (
                            self._is_epub_document(item)
                            and name not in seen_names
                        ):
                            items.append(item)
                            seen_names.add(name)

                # -----------------------------------------------
                # 2. Одна лента абзацев и глобальные позиции
                # -----------------------------------------------

                all_paragraphs = []
                doc_info = {}
                document_order = []
                global_headings = {}

                for item in items:
                    name = item.get_name()
                    offset = len(all_paragraphs)

                    extracted = self._extract_epub_document(
                        item.get_content()
                    )

                    paragraphs = extracted["paragraphs"]

                    # Пустые документы не формируют пустые главы.
                    if not paragraphs:
                        continue

                    doc_info[name] = {
                        "offset": offset,
                        "extracted": extracted,
                    }

                    document_order.append(name)
                    all_paragraphs.extend(paragraphs)

                    for local_position, heading in (
                        extracted["headings"].items()
                    ):
                        global_headings[offset + local_position] = heading

                if not all_paragraphs:
                    raise ValueError("В EPUB не удалось найти текст.")

                # -----------------------------------------------
                # 3. TOC и якоря
                # -----------------------------------------------

                toc_entries = []
                self._flatten_epub_toc(
                    book_epub.toc or [],
                    toc_entries,
                )

                units = self._build_epub_units(
                    all_paragraphs,
                    document_order,
                    doc_info,
                    global_headings,
                    toc_entries,
                )

                chapters_data = self._normalize_chapter_units(units)

                language = self._resolve_book_language(
                    metadata_language,
                    chapters_data,
                )

                logger.info(
                    "EPUB parsed: title=%r documents=%d chapters=%d language=%s",
                    title,
                    len(document_order),
                    len(chapters_data),
                    language,
                )

                return {
                    "title": title,
                    "description": description,
                    "language": language,
                    "authors": authors,
                    "genres": genres,
                    "chapters": chapters_data,
                    "cover_content": cover_content,
                    "cover_filename": cover_filename,
                }

        except ValueError:
            raise

        except Exception as exc:
            logger.exception("Ошибка парсинга EPUB")

            raise ValueError(
                f"Не удалось обработать EPUB: {exc}"
            ) from exc


    def _save_epub_chapters(self, book, chapters_data):
        """Массовое сохранение глав EPUB."""
        ChapterModel = book.chapters.model

        objects = [
            ChapterModel(
                book=book,
                title=chapter["title"],
                content=chapter["content"],
                order=chapter["order"],
            )
            for chapter in chapters_data
        ]

        if objects:
            ChapterModel.objects.bulk_create(
                objects,
                batch_size=500,
            )


class UserProfileViewSet(
    viewsets.mixins.RetrieveModelMixin,
    viewsets.mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    parser_classes = (MultiPartParser, FormParser, JSONParser)
    serializer_class = UserProfileSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_value_regex = '[0-9]+'

    def get_serializer_class(self):
        if self.action in ['update', 'partial_update']:
            return UserProfileWriteSerializer
        return super().get_serializer_class()
    
    def get_object(self):
        if getattr(self, 'swagger_fake_view', False):
            return UserProfile.objects.none()
        return self.request.user.profile

    def partial_update(self, request, *args, **kwargs):
        super().partial_update(request, *args, **kwargs)

        serializer = UserProfileSerializer(
            self.get_object(),
            context={"request": request},
        )
        return Response(serializer.data)


class ConversationViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    lookup_value_regex = '[0-9]+'

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Book.objects.none()
        user_queryset = Conversation.objects.filter(user=self.request.user)
        last_message = Message.objects.filter(
            conversation=OuterRef("pk")
        ).order_by("-timestamp")
        annotate_queryset = user_queryset.annotate(messages_count=Count("messages"), 
                                                   last_message_text=Subquery(last_message.values("content")[:1]),
                                                   last_message_created_at=Subquery(last_message.values("timestamp")[:1]))
        return annotate_queryset.order_by("-last_message_created_at")

    def get_serializer_class(self):
        if self.action == "list":
            return ConversationListSerializer
        
        if self.action == "create":
            return CreateConversationSerializer
        
        return ConversationSerializer

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @extend_schema(
            request=SendMessageSerializer,
            summary="Send message",
            description="Create a message from user, recieved a response from AI and return both messages"
    )
    @action(detail=True, methods=["post"])
    def send_message(self, request, pk=None):

        conversation = self.get_object()
        serializer = SendMessageSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user_message = serializer.validated_data["message"]

        if not user_message:
            return Response(
                {"error": "Сообщение не может быть пустым"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            user_profile = get_object_or_404(UserProfile, user=request.user)

            user_msg = Message.objects.create(
                conversation=conversation, role="user", content=user_message
            )

            messages = list(conversation.messages.order_by("timestamp", "id"))

            ai_service = AITeacherService()

            ai_response = ai_service.generate_response(
               conversation, user_profile, messages[:-1], user_message
            )

            ai_msg = Message.objects.create(
                conversation=conversation, role="assistant", content=ai_response
            )

            if conversation.messages.count() == 2 and not conversation.title:
                conversation.title = user_message[:50] + (
                    "..." if len(user_message) > 50 else ""
                )
                conversation.save()

            return Response(
                {
                    "user_message": MessageSerializer(user_msg).data,
                    "ai_response": MessageSerializer(ai_msg).data,
                }
            )

        except UserProfile.DoesNotExist:
            return Response(
                {
                    "error": "Профиль пользователя не найден. Создайте профиль перед началом разговора."
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

    @action(detail=True, methods=["get"])
    def messages(self, request, pk=None):
        conversation = self.get_object()
        queryset = conversation.messages.order_by("-timestamp", "-id")

        paginator = MessagesPagination()

        page = paginator.paginate_queryset(queryset, request, view=self)
        if page is not None:
            serializer = MessageSerializer(page, many=True)
            return paginator.get_paginated_response(serializer.data)

        serializer = MessageSerializer(queryset, many=True)
        return Response(serializer.data)

RAW_TEXT_MAX_LEN = 200

class TranslationViewSet(mixins.CreateModelMixin,
        mixins.ListModelMixin,
        mixins.RetrieveModelMixin,
        mixins.DestroyModelMixin,
        viewsets.GenericViewSet,
    ):
    serializer_class = TranslationRequestSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["translator_service", "target_language", "source_language"]
    search_fields = ["original_text", "translated_text"]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]

    def get_throttles(self):
        if self.action == "create":
            throttle_classes = [
                TranslationThrottle,
                UserRateThrottle,
            ]

        elif self.action == "word_analysis":
            throttle_classes = [
                WordAnalysisThrottle,
                UserRateThrottle,
            ]

        else:
            throttle_classes = []

        return [throttle() for throttle in throttle_classes]

    def get_serializer_class(self):
        if self.action == "create":
            return TranslationRequestSerializer
        return TranslationSerializer

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Translation.objects.none()

        queryset = (
            Translation.objects
            .filter(user=self.request.user)
            .select_related("word_analysis")
        )

        has_analysis = self.request.query_params.get("has_analysis")

        if has_analysis == "true":
            queryset = queryset.filter(word_analysis__isnull=False)
        elif has_analysis == "false":
            queryset = queryset.filter(word_analysis__isnull=True)

        return queryset

    def create(self, request, *args, **kwargs):
        try:
            target_language = request.user.profile.native_language
            if not target_language:
                raise AttributeError
        except (AttributeError, TypeError):
            return Response(
                {
                    "success": False,
                    "error": "Не удалось определить ваш язык. Пожалуйста, укажите его в профиле.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        serializer_class = self.get_serializer_class()
        serializer = serializer_class(
            data=request.data, context={"target_language": target_language}
        )
        serializer.is_valid(raise_exception=True)
        validated_data = serializer.validated_data
        try:
            translation_service = TranslationService(user=request.user)
            service_kwargs = {
                "text": validated_data["text"],
                "target_language": validated_data["target_language"],
                "source_language": validated_data["source_language"],
                "context": validated_data.get("context", ""),
                "service": validated_data.get("service", "auto"),
            }
            result = translation_service.translate(**service_kwargs)
            response_serializer = TranslationResponseSerializer(result)
            return Response(response_serializer.data, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Unexpected error in translation: {e}")
            return Response(
                {"success": False, "error": _("Внутренняя ошибка сервера")},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["post"], url_path="word-analysis")
    def word_analysis(self, request, pk=None):
        translation = self.get_object()

        analysis = WordAnalysisService().get_or_generate(
            translation=translation
        )

        serializer = WordAnalysisSerializer(analysis)
        return Response(
            serializer.data,
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="known-words")
    def known_words(self, request):
        """
        Уникальные слова, которые пользователь ранее переводил
        на языке (source_language) указанной книги.
        GET /translations/known-words/?book_id=123  ->  ["beautiful", "house", "make"]
        """
        params = KnownWordsQuerySerializer(data=request.query_params)
        params.is_valid(raise_exception=True)

        book = get_object_or_404(
            Book.objects.only("id", "language"),
            pk=params.validated_data["book_id"],
            user=request.user,
        )

        raw_texts = (
            Translation.objects.filter(
                user=request.user,
                source_language=book.language,
            )
            .annotate(text_len=Length("original_text"))
            .filter(text_len__lte=RAW_TEXT_MAX_LEN)
            .order_by()  # сбрасываем Meta.ordering, иначе distinct() не сработает
            .values_list("original_text", flat=True)
            .distinct()
            .iterator(chunk_size=2000)
        )

        words = {w for t in raw_texts if (w := normalize_word(t))}
        return Response(sorted(words))

