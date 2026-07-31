import io
import logging
import re
import tempfile
import traceback
import xml.etree.ElementTree as ET
import zipfile

import ebooklib
from bs4 import BeautifulSoup
from django.contrib.auth import authenticate
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Case, F, When, Count, OuterRef, Subquery
from django.db.models.functions import Lower
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django_filters.rest_framework import DjangoFilterBackend
from ebooklib import ITEM_COVER, epub
from langdetect import LangDetectException, detect
from rest_framework import generics, permissions, serializers, status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_extensions.cache.mixins import CacheResponseMixin
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken
from drf_spectacular.utils import extend_schema, OpenApiParameter
from drf_spectacular.types import OpenApiTypes

from reader.exceptions import TranslationServiceError
from reader.services.ai_service import AITeacherService
from reader.services.translation_service import TranslationService
from reader.throttles import TranslationThrottle
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
)

logger = logging.getLogger(__name__)


class AuthViewSet(viewsets.GenericViewSet):
    """ViewSet для авторизации"""

    permission_classes = [AllowAny]
    serializer_class = GoogleAuthSerializer

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
        refresh_token = request.data.get("refresh")
        if not refresh_token:
            return Response(
                {"error": "Refresh token is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            refresh = RefreshToken(refresh_token)
            return Response(
                {"access": str(refresh.access_token)}, status=status.HTTP_200_OK
            )
        except Exception as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)


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
        if getattr(self, 'swagger_fake_view', False):
            return Book.objects.none()
        user_queryset = Book.objects.filter(user=self.request.user)
        annotate_queryset = user_queryset.annotate(chapter_count=Count("chapters"))
        return annotate_queryset.order_by("-uploaded_at")


    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def perform_delete(self, instance):
        book_name = instance.title
        book_user = instance.user
        logger.info(f"Book {book_name} for {book_user} were deleted")

        instance.delete()

    @action(
        detail=False,
        methods=["post"],
        url_path="upload",
        parser_classes=[MultiPartParser],
    )
    @transaction.atomic
    def upload_book(self, request):
        """
        Единый эндпоинт для загрузки и парсинга книжных файлов.
        Автоматически определяет формат (.fb2, .epub, .zip) и использует
        сериализатор для валидации входных данных.
        """
        # ШАГ 1: Валидация входных данных через исправленный сериализатор
        # get_serializer_class должен вернуть BookUploadSerializer для этого action
        upload_serializer = self.get_serializer(data=request.data)
        try:
            upload_serializer.is_valid(raise_exception=True)
        except serializers.ValidationError as e:
            return Response(e.detail, status=status.HTTP_400_BAD_REQUEST)

        # Теперь мы уверены, что файл прошел валидацию
        uploaded_file = upload_serializer.validated_data["file"]

        original_filename = uploaded_file.name
        filename_lower = original_filename.lower()

        file_content = None
        book_format = None

        try:
            # --- ШАГ 2: Определение формата и извлечение содержимого ---
            file_bytes = uploaded_file.read()

            if filename_lower.endswith(".zip"):
                with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
                    fb2_files = [f for f in zf.namelist() if f.lower().endswith(".fb2")]
                    epub_files = [
                        f for f in zf.namelist() if f.lower().endswith(".epub")
                    ]

                    if fb2_files:
                        file_to_extract = fb2_files[0]
                        file_content = zf.read(file_to_extract)
                        book_format = "FB2"
                    elif epub_files:
                        file_to_extract = epub_files[0]
                        file_content = zf.read(file_to_extract)
                        book_format = "EPUB"
                    else:
                        raise ValueError(
                            "В ZIP-архиве не найдены поддерживаемые файлы (.fb2, .epub)."
                        )

            elif filename_lower.endswith(".fb2"):
                file_content = file_bytes
                book_format = "FB2"

            elif filename_lower.endswith(".epub"):
                file_content = file_bytes
                book_format = "EPUB"

            # --- ШАГ 3: Вызов соответствующего парсера ---
            if book_format == "FB2":
                book_data = self._parse_fb2_content(file_content)
            elif book_format == "EPUB":
                book_data = self._parse_epub_content(file_content)
            else:
                raise ValueError("Не удалось определить формат книги для парсинга.")

            # --- ШАГ 3.5: Унификация данных от парсеров ---
            authors = ""
            genres = ""
            if book_format == "FB2":
                authors = book_data.get("additional_fields", {}).get("authors", "")
                genres = book_data.get("additional_fields", {}).get("genres", "")
            else:  # Для EPUB
                authors = book_data.get("authors", "")
                genres = book_data.get("genres", "")

            # --- ШАГ 4: Создание и сохранение объекта книги ---
            book = Book.objects.create(
                user=request.user,
                title=book_data.get("title", "Без названия"),
                description=book_data.get("description", ""),
                book_format=book_format,
                authors=authors,
                genres=genres,
                language=book_data.get("language", "unknown"),
                file_size=len(file_content),
            )

            if book_format == "EPUB" and book_data.get("cover_content"):
                book.cover.save(
                    book_data["cover_filename"],
                    ContentFile(book_data["cover_content"]),
                    save=False,
                )

            book.file.save(original_filename, ContentFile(file_content), save=False)
            book.save()

            # --- ШАГ 5: Сохранение глав ---
            if book_format == "FB2":
                self._parse_fb2_chapters(book, book_data.get("chapters", []))
            elif book_format == "EPUB":
                self._save_epub_chapters(book, book_data.get("chapters", []))

            # --- ШАГ 6: Финальный ответ ---
            result_serializer = BookDetailSerializer(book, context={"request": request})
            return Response(
                {"message": "Книга успешно загружена", "book": result_serializer.data},
                status=status.HTTP_201_CREATED,
            )

        except ValueError as e:
            logger.error(f"Ошибка формата или обработки файла: {str(e)}")
            return Response(
                {"error": f"Ошибка формата или обработки файла: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            # exc_info=True добавит полный traceback ошибки в логи
            logger.error(
                f"Непредвиденная ошибка при загрузке файла: {str(e)}", exc_info=True
            )
            return Response(
                {
                    "error": "Произошла непредвиденная ошибка на сервере при обработке файла."
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
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

    def _parse_fb2_content(self, file_content):
        """
        Парсинг FB2 файла из содержимого (bytes)
        """
        try:
            # Парсим XML из байтов
            root = ET.fromstring(file_content)

            # Определяем namespace
            namespace = {"fb": "http://www.gribuser.ru/xml/fictionbook/2.0"}

            # Ищем namespace в корневом элементе
            if root.tag.startswith("{"):
                ns_uri = root.tag[1 : root.tag.find("}")]
                namespace = {"fb": ns_uri}
            else:
                # Если namespace не найден, пробуем без него
                namespace = {}

            # Валидируем структуру FB2
            self._validate_fb2_structure(root, namespace)

            # Извлекаем метаданные
            book_data = self._extract_fb2_metadata(root, namespace)

            # Извлекаем главы
            book_data["chapters"] = self._extract_fb2_chapters(root, namespace)

            if not book_data.get("language"):
                text_sample = ""
                for chapter in book_data.get("chapters", []):
                    text_sample += chapter.get("content", []) + "\n"
                    if len(text_sample) > 2000:
                        break

                text_sample = text_sample[:2000]
                if text_sample.strip():
                    try:
                        detected_language = detect(text_sample)
                        book_data["language"] = detected_language
                    except LangDetectException:
                        book_data["language"] = "unknown"
                else:
                    book_data["language"] = "unknown"

            return book_data

        except ET.ParseError as e:
            raise ValueError(f"Некорректный XML формат: {str(e)}")
        except Exception as e:
            raise ValueError(f"Ошибка при парсинге FB2: {str(e)}")

    def _validate_fb2_structure(self, root, namespace):
        """
        Проверка базовой структуры FB2
        """
        required_elements = [
            (".//fb:title-info" if namespace else ".//title-info", "title-info"),
            (".//fb:body" if namespace else ".//body", "body"),
        ]

        for xpath, element_name in required_elements:
            if root.find(xpath, namespace) is None:
                raise ValueError(
                    f"FB2 файл поврежден: отсутствует элемент {element_name}"
                )

    def _extract_fb2_metadata(self, root, namespace):
        """
        Извлечение метаданных из FB2
        """

        def find_text(element, path, default=""):
            """Безопасный поиск текста в элементе"""
            if namespace:
                path = path.replace("/", "/fb:").replace("fb:fb:", "fb:")
                if not path.startswith("fb:"):
                    path = "fb:" + path

            found = element.find(path, namespace)
            return found.text.strip() if found is not None and found.text else default

        def find_all_text(element, path):
            """Поиск всех элементов и объединение текста"""
            if namespace:
                path = path.replace("/", "/fb:").replace("fb:fb:", "fb:")
                if not path.startswith("fb:"):
                    path = "fb:" + path

            elements = element.findall(path, namespace)
            return " ".join([el.text.strip() for el in elements if el.text])

        # Основные метаданные
        title_info = root.find(
            ".//fb:title-info" if namespace else ".//title-info", namespace
        )

        if title_info is None:
            raise ValueError("Не найдена секция title-info в FB2 файле")

        # Извлекаем заголовок
        title = find_text(title_info, "book-title", "Без названия")

        # Извлекаем авторов
        authors = []
        author_elements = title_info.findall(
            ".//fb:author" if namespace else ".//author", namespace
        )
        for author in author_elements:
            first_name = find_text(author, "first-name")
            last_name = find_text(author, "last-name")
            middle_name = find_text(author, "middle-name")

            author_name = " ".join(filter(None, [first_name, middle_name, last_name]))
            if author_name:
                authors.append(author_name)

        # Извлекаем аннотацию
        annotation_elem = title_info.find(
            ".//fb:annotation" if namespace else ".//annotation", namespace
        )
        description = ""
        if annotation_elem is not None:
            # Собираем текст из всех параграфов аннотации
            paragraphs = annotation_elem.findall(
                ".//fb:p" if namespace else ".//p", namespace
            )
            if paragraphs:
                description = "\n".join([p.text.strip() for p in paragraphs if p.text])
            else:
                # Если нет параграфов, берем весь текст
                description = "".join(annotation_elem.itertext()).strip()

        # Извлекаем жанры
        genres = find_all_text(title_info, "genre")

        # Извлекаем язык
        language = find_text(title_info, "lang", "")

        return {
            "title": title,
            "description": description,
            "language": language,
            "additional_fields": {
                "authors": ", ".join(authors) if authors else "",
                "genres": genres,
            },
        }

    def _extract_fb2_chapters(self, root, namespace):
        """
        Извлечение глав из FB2
        """
        chapters = []

        # Ищем body элемент
        body = root.find(".//fb:body" if namespace else ".//body", namespace)
        if body is None:
            return chapters

        # Ищем только прямые дочерние секции первого уровня
        sections = body.findall("./fb:section" if namespace else "./section", namespace)

        # Если нет прямых секций, ищем все секции
        if not sections:
            sections = body.findall(
                ".//fb:section" if namespace else ".//section", namespace
            )

        chapter_order_counter = 1
        for section in sections:
            chapter_data = self._process_fb2_section(
                section, namespace, chapter_order_counter
            )

            if chapter_data:
                chapters.append(chapter_data)
                chapter_order_counter += 1

        return chapters

    def _process_fb2_section(self, section, namespace, order):
        """
        Обработка отдельной секции FB2
        """
        # Извлекаем заголовок главы
        title_elem = section.find("./fb:title" if namespace else "./title", namespace)
        chapter_title = ""

        if title_elem is not None:
            # Собираем текст из всех параграфов заголовка
            title_paragraphs = title_elem.findall(
                ".//fb:p" if namespace else ".//p", namespace
            )
            if title_paragraphs:
                chapter_title = " ".join(
                    [p.text.strip() for p in title_paragraphs if p.text]
                )
            else:
                chapter_title = "".join(title_elem.itertext()).strip()

        if not chapter_title:
            chapter_title = f"Глава {order}"

        # Извлекаем содержимое главы (только параграфы, не включая вложенные секции)
        content_paragraphs = []
        for child in section:
            if child.tag.endswith("p") or (
                namespace and child.tag == f"{{{namespace['fb']}}}p"
            ):
                content_paragraphs.append(child)

        chapter_content = "\n\n".join(
            [
                "".join(p.itertext()).strip()
                for p in content_paragraphs
                if "".join(p.itertext()).strip()
            ]
        )

        if chapter_content.strip():
            return {"title": chapter_title, "content": chapter_content, "order": order}
        return None

    def _parse_fb2_chapters(self, book, chapters_data):
        """
        Создание объектов глав в базе данных
        """
        Chapter = book.chapters.model

        for chapter_data in chapters_data:
            Chapter.objects.create(
                book=book,
                title=chapter_data["title"],
                content=chapter_data["content"],
                order=chapter_data["order"],
            )

    def _detect_language_from_content(self, chapters_data):
        """
        Определяет язык по содержимому первых глав книги.

        Args:
            chapters_data (list): Список словарей с главами, где у каждого есть ключ 'content'.

        Returns:
            str | None: Код языка (например, 'es', 'en') в случае успеха
                        или None, если определить не удалось.
        """
        # Собираем достаточно большой фрагмент текста для более точного определения
        # langdetect работает лучше на текстах от 1000 до 3000 символов
        text_sample = "".join(ch.get("content", "") for ch in chapters_data[:5])[:3000]

        if not text_sample.strip():
            logger.warning("Не удалось извлечь текст для определения языка.")
            return None

        try:
            # Пытаемся определить язык
            return detect(text_sample)
        except LangDetectException:
            # Это исключение возникает, если текст слишком короткий,
            # неоднозначный или не похож ни на один известный язык.
            logger.warning(
                "Не удалось определить язык по содержимому (LangDetectException)."
            )
            return None

    def _parse_epub_content(self, file_content):
        """
        Парсинг EPUB файла из содержимого (bytes).
        Использует временный файл и защищен от некорректных метаданных.
        """
        with tempfile.NamedTemporaryFile(suffix=".epub", delete=True) as tmp:
            try:
                tmp.write(file_content)
                tmp.flush()
                book_epub = epub.read_epub(tmp.name)

                title = "Без названия"
                if book_epub.get_metadata("DC", "title"):
                    title = book_epub.get_metadata("DC", "title")[0][0]

                raw_authors = book_epub.get_metadata("DC", "creator")
                authors_list = (
                    [author[0] for author in raw_authors if author and author[0]]
                    if raw_authors
                    else []
                )

                raw_genres = book_epub.get_metadata("DC", "subject")
                genres_list = (
                    [genre[0] for genre in raw_genres if genre and genre[0]]
                    if raw_genres
                    else []
                )
                # =========================================================================

                description = ""
                if book_epub.get_metadata("DC", "description"):
                    desc_html = book_epub.get_metadata("DC", "description")[0][0]
                    description = BeautifulSoup(desc_html, "lxml").get_text(strip=True)

                # --- Извлечение языка из метаданных (без присвоения основной переменной) ---
                metadata_language = (
                    book_epub.get_metadata("DC", "language")[0][0]
                    if book_epub.get_metadata("DC", "language")
                    else None
                )

                cover_content, cover_filename = (None, None)
                cover_items = book_epub.get_items_of_type(ITEM_COVER)
                try:
                    cover_item = next(cover_items)
                    cover_content = cover_item.get_content()
                    cover_filename = cover_item.get_name().split("/")[-1]
                except StopIteration:
                    pass

                # ------------------ БЕЗОПАСНЫЙ ОБХОД TOC (без изменений) ------------------
                def _iter_toc_entries(toc):
                    for entry in toc:
                        if isinstance(entry, (list, tuple)):
                            if len(entry) >= 1:
                                yield entry[0]
                            if len(entry) >= 2 and entry[1]:
                                for child in _iter_toc_entries(entry[1]):
                                    yield child
                        else:
                            yield entry

                toc_map = {}
                for node in _iter_toc_entries(book_epub.toc or []):
                    href = getattr(node, "href", None)
                    if isinstance(node, str) and not href:
                        href = node
                    title_val = getattr(node, "title", None)
                    if isinstance(title_val, (list, tuple)) and title_val:
                        title_val = title_val[0]
                    if isinstance(title_val, (bytes, bytearray)):
                        try:
                            title_val = title_val.decode("utf-8", errors="ignore")
                        except Exception:
                            title_val = str(title_val)
                    if isinstance(title_val, str):
                        title_val = BeautifulSoup(title_val, "lxml").get_text(
                            strip=True
                        )
                    if href:
                        clean_href = href.split("#")[0]
                        if clean_href not in toc_map or (
                            title_val and toc_map.get(clean_href) == ""
                        ):
                            toc_map[clean_href] = title_val or ""
                # --------------------------------------------------------------------------

                chapters_data = []
                chapter_order = 1
                for item in book_epub.get_items_of_type(ebooklib.ITEM_DOCUMENT):
                    content_html = item.get_content()
                    soup = BeautifulSoup(content_html, "xml")
                    for tag in soup(["script", "style"]):
                        tag.decompose()
                    text_blocks = [
                        p.get_text(strip=True)
                        for p in soup.find_all(
                            ["p", "h1", "h2", "h3", "h4", "h5", "h6"]
                        )
                    ]
                    chapter_content = "\n\n".join(filter(None, text_blocks))
                    if chapter_content.strip():
                        clean_href = item.get_name().split("#")[0]
                        chapter_title = toc_map.get(
                            clean_href, f"Глава {chapter_order}"
                        )
                        chapters_data.append(
                            {
                                "title": chapter_title,
                                "content": chapter_content,
                                "order": chapter_order,
                            }
                        )
                        chapter_order += 1

                # =========================================================================
                # НОВАЯ УЛУЧШЕННАЯ ЛОГИКА ОПРЕДЕЛЕНИЯ ЯЗЫКА
                # =========================================================================
                # Шаг 1: Всегда пытаемся определить язык по реальному содержимому книги
                detected_language = self._detect_language_from_content(chapters_data)

                # Шаг 2: Выбираем итоговый язык на основе приоритетов
                if detected_language:
                    # Самый высокий приоритет: язык, определенный по тексту
                    language = detected_language
                elif metadata_language:
                    # Второй приоритет: язык из метаданных, если по тексту не вышло
                    language = metadata_language
                else:
                    # Запасной вариант, если ничего не найдено
                    language = "unknown"

                logger.info(
                    f"Определение языка для книги '{title[:30]}...': "
                    f"По содержимому: {detected_language}, "
                    f"Из метаданных: {metadata_language}, "
                    f"Итоговый: {language}"
                )
                # =========================================================================

                return {
                    "title": title,
                    "description": description,
                    "language": language,  # Здесь теперь используется надежно определенный язык
                    "authors": ", ".join(authors_list),
                    "genres": ", ".join(genres_list),
                    "chapters": chapters_data,
                    "cover_content": cover_content,
                    "cover_filename": cover_filename,
                }
            except Exception as e:
                logger.error(
                    f"Ошибка при парсинге EPUB из временного файла: {str(e)}",
                    exc_info=True,
                )
                raise ValueError(f"Ошибка при парсинге EPUB: {str(e)}")

    def _save_epub_chapters(self, book, chapters_data):
        """
        Создание объектов глав в базе данных из распарсенных данных EPUB.
        """
        Chapter = book.chapters.model
        chapters_to_create = [Chapter(book=book, **data) for data in chapters_data]
        if chapters_to_create:
            Chapter.objects.bulk_create(chapters_to_create)


class FlashCardViewSet(viewsets.ModelViewSet):
    serializer_class = FlashCardSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, OrderingFilter]
    filterset_class = FlashCardFilter
    ordering_fields = [
        "next_review",  # По дате следующего показа (самое полезное)
        "created_at",  # По дате создания (новые/старые)
        "word",  # По алфавиту
        "interval",  # Ваше предложение! (от самых "трудных" к "легким")
        "repetitions",  # По количеству повторений
        "ease_factor",
    ]
    ordering = ["next_review"]

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return FlashCard.objects.none()

        queryset = FlashCard.objects.filter(user=self.request.user)

        queryset = queryset.annotate(
            is_learning_order=Case(When(is_learning=True, then=0), default=1)
        ).order_by("is_learning_order", "next_review")
        return queryset

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=True, methods=["post"], url_path="answer")
    def submit_answer(self, request, pk=None):
        """
        Обрабатывает ответ пользователя (знаю/не знаю).
        Ожидает в теле запроса: {"knows": true} или {"knows": false}
        """
        card = self.get_object()
        knows_word = request.data.get("knows")

        if knows_word is None or not isinstance(knows_word, bool):
            return Response(
                {"error": "В теле запроса ожидается поле 'knows' (true/false)."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        card.update_review_data(knows_word)
        serializer = self.get_serializer(
            card
        )  # Возвращаем обновленное состояние карточки
        return Response(serializer.data)

    @action(detail=False, methods=["get"])
    def stats(self, request):
        """Возвращает актуальную статистику по карточкам."""
        statistics = FlashCard.get_review_stats(request.user)
        return Response(statistics)

    @action(detail=False, methods=["post"], url_path="reset")
    def reset_progress(self, request):
        """Сбрасывает прогресс изучения для всех карточек пользователя."""
        cards_reset_count = FlashCard.reset_all_progress(request.user)
        return Response(
            {"message": f"Прогресс сброшен для {cards_reset_count} карточек."}
        )

    @action(detail=False, methods=["post"], url_path="get-suggestions")
    def get_suggestions(self, request):
        logger.info("--- [DEBUG VIEW] ВХОД В get_suggestions УСПЕШЕН ---")
        logger.info(f"[DEBUG VIEW] Получены данные запроса: {request.data}")
        request_serializer = SuggestionRequestSerializer(data=request.data)
        request_serializer.is_valid(raise_exception=True)

        user = request.user
        try:

            target_language = user.profile.native_language
            if not target_language:
                raise AttributeError
        except AttributeError:
            return Response(
                {"error": "В вашем профиле не указан язык для изучения."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        word_to_suggest = request_serializer.validated_data["word"]
        try:
            service = TranslationService(user=request.user)
            suggestions_data = service.get_suggestions_for_flashcard(
                word=word_to_suggest, target_language=target_language
            )

            response_serializer = SuggestionResponseSerializer(data=suggestions_data)
            response_serializer.is_valid(raise_exception=True)
            return Response(response_serializer.data, status=status.HTTP_200_OK)
        except Exception as e:
            tb_str = traceback.format_exc()

            # Выводим в лог гораздо более подробную информацию
            logger.error("--- НАЧАЛО ПОЛНОГО ТРЕЙСБЕКА ОШИБКИ ---")
            logger.error(tb_str)
            logger.error("--- КОНЕЦ ПОЛНОГО ТРЕЙСБЕКА ОШИБКИ ---")
            logger.error(f"Исходное сообщение об ошибке: {e}")
            logger.error(f"Неожиданная ошибка в get_suggestions: {e}")
            return Response(
                {"error": _("Внутренняя ошибка сервера при получении предложений")},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
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


class DictionaryEntryViewSet(CacheResponseMixin, viewsets.ReadOnlyModelViewSet):
    """
    ViewSet для получения списка словарных статей и выполнения действий над ними.
    Используется CacheResponseMixin для демонстрации кэширования API-ответов.
    Кэшируется только GET-запросы (list/retrieve).
    """

    queryset = DictionaryEntry.objects.prefetch_related("categories").annotate(word_lower=Lower("word")).order_by(
        ("word_lower"), "id"
    )
    serializer_class = DictionaryEntrySerializer
    permission_classes = [permissions.IsAuthenticatedOrReadOnly]
    pagination_class = DictionaryPagination
    filter_backends = [DjangoFilterBackend, SearchFilter]
    filterset_fields = ["level", "categories__slug"]
    search_fields = ["word", "definition"]

    @action(detail=True, methods=["post"], url_path="translate")
    def translate(self, request, pk=None):
        """
        Кастомное действие для перевода конкретного слова.
        Вызывается по URL: POST /api/dictionary/{pk}/translate/
        """
        entry = self.get_object()
        word_to_translate = entry.word

        target_language = None
        if request.user.is_authenticated and hasattr(request.user, "profile"):
            target_language = request.user.profile.native_language

        if not target_language:
            target_language = "ru" 

        source_language = "en"

        try:
            translation_service = TranslationService(
                user=request.user if request.user.is_authenticated else None
            )
            print(
                f"[DEBUG] Пытаюсь перевести: '{word_to_translate}' на язык '{target_language}'"
            )

            result = translation_service.translate(
                text=word_to_translate,
                target_language=target_language,
                source_language=source_language,
                service="deepl",
            )
            print(f"[DEBUG] Ответ от TranslationService: {result}")

            response_data = {"translation": result.get("translated_text", "")}
            print(f"[DEBUG] Подготовленные данные для ответа: {response_data}")
            serializer = DictionaryTranslationResponseSerializer(response_data)
            return Response(serializer.data, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {"error": f"Внутренняя ошибка сервера: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class DictionaryCategoryListView(generics.ListAPIView):
    """
    View для получения списка всех категорий словаря.
    """

    queryset = DictionaryCategory.objects.all().order_by("name", "id")
    serializer_class = DictionaryCategorySerializer
    permission_classes = [permissions.AllowAny]
    pagination_class = None


class ConversationViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    lookup_value_regex = '[0-9]+'
    pagination_class = MessagesPagination

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

            messages = list(conversation.messages.order_by("timestamp"))

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


class TranslateView(generics.CreateAPIView):
    serializer_class = TranslationRequestSerializer
    permission_classes = [IsAuthenticated]
    throttle_classes = [TranslationThrottle, UserRateThrottle]

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


class TranslationHistoryListView(generics.ListAPIView):
    serializer_class = TranslationSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["translator_service", "target_language", "source_language"]
    search_fields = ["original_text", "translated_text"]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Translation.objects.none()
        return Translation.objects.filter(user=self.request.user)


class TranslationDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = TranslationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        if getattr(self, 'swagger_fake_view', False):
            return Book.objects.none()
        return Translation.objects.filter(user=self.request.user)
