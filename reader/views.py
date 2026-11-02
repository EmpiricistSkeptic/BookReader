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

from .pagination import MessagesPagination
from .models import (
    Book,
    Chapter,
    Conversation,
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
    GoogleAuthSerializer,
    LoginSerializer,
    MessageSerializer,
    SendMessageSerializer,
    RegisterSerializer,
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

from .services.book_services import BookImportError, BookImportService

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

    @action(
        detail=False,
        methods=["post"],
        url_path="upload",
        parser_classes=[MultiPartParser],
    )
    def upload_book(self, request):
        """
        Загружает FB2, EPUB или ZIP с одной поддерживаемой книгой.

        Вся логика импорта находится в BookImportService.
        Формат существующего API не меняется.
        """
        upload_serializer = self.get_serializer(data=request.data)
        upload_serializer.is_valid(raise_exception=True)

        uploaded_file = upload_serializer.validated_data["file"]

        result_data = None

        def build_result(book):
            nonlocal result_data

            result_data = BookDetailSerializer(
                book,
                context={"request": request},
            ).data

        try:
            book = BookImportService().import_book(
                user=request.user,
                uploaded_file=uploaded_file,
                before_commit=build_result,
            )

        except BookImportError as exc:
            return Response(
                {"error": f"Ошибка обработки книги: {exc}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        except Exception:
            logger.exception(
                "Непредвиденная ошибка при загрузке книги %r",
                uploaded_file.name,
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

        return Response(
            {
                "message": "Книга успешно загружена",
                "book": result_data,
            },
            status=status.HTTP_201_CREATED,
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

