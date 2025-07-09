import xml.etree.ElementTree as ET
import zipfile
import io

from rest_framework import viewsets, permissions, status, generics
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.throttling import UserRateThrottle
from django.core.cache import cache
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework_simplejwt.tokens import RefreshToken
from django.contrib.auth import authenticate
from django.core.files.base import ContentFile


from rest_framework.parsers import MultiPartParser
from django.db import transaction
import logging

from .models import (
    Book,
    FlashCard,
    UserProfile,
    DictionaryEntry,
    Chapter,
    Conversation,
    Message,
)
from .serializers import (
    BookCreateUpdateSerializer,
    FlashCardSerializer,
    UserProfileSerializer,
    DictionaryEntrySerializer,
    GoogleAuthSerializer,
    UserSerializer,
    ChapterDetailSerializer,
    ChapterListSerializer,
    BookDetailSerializer,
    BookListSerializer,
    BookUploadSerializer,
    ConversationSerializer,
    ConversationListSerializer,
    MessageSerializer,
    TranslationRequestSerializer,
    TranslationResponseSerializer,
    TranslationSerializer,
)
from reader.utils.google_auth import GoogleAuthService

from reader.services.ai_service import AITeacherService
from reader.services.translation_service import TranslationService
from reader.throttles import TranslationThrottle
from reader.exceptions import TranslationServiceError

logger = logging.getLogger(__name__)


class AuthViewSet(viewsets.GenericViewSet):
    """ViewSet для авторизации"""

    permission_classes = [AllowAny]

    @action(detail=False, methods=["post"], url_path="google")
    def google_auth(self, request):
        serializer = GoogleAuthSerializer(data=request.data)
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

    def get_serializer_class(self):
        if self.action == "list":
            return BookListSerializer
        elif self.action == "retrieve":
            return BookDetailSerializer
        elif self.action == "upload_fb2":
            return BookUploadSerializer
        else:
            return BookCreateUpdateSerializer

    def get_queryset(self):
        return Book.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=False, methods=["post"], parser_classes=[MultiPartParser])
    @transaction.atomic
    def upload_fb2(self, request):
        """
        Загрузка и парсинг FB2 файла
        """
        try:
            # Проверяем наличие файла
            if "file" not in request.FILES:
                return Response(
                    {"error": "Файл не найден"}, status=status.HTTP_400_BAD_REQUEST
                )

            uploaded_file = request.FILES["file"]

            # Проверяем расширение файла
            if not uploaded_file.name.lower().endswith(".fb2"):
                return Response(
                    {"error": "Поддерживается только формат FB2"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

            # Читаем содержимое файла один раз
            file_content = uploaded_file.read()

            # Парсим FB2 файл из содержимого
            book_data = self._parse_fb2_content(file_content)

            # Создаем объект книги
            book = Book.objects.create(
                user=request.user,
                title=book_data["title"],
                description=book_data["description"],
                book_format="FB2",
                authors=book_data.get("additional_fields", {}).get("authors", ""),
                genres=book_data.get("additional_fields", {}).get("genres", ""),
                language=book_data.get("additional_fields", {}).get("language", "en"),
                file_size=len(file_content),
            )

            # Сохраняем файл из того же содержимого
            book.file.save(uploaded_file.name, ContentFile(file_content), save=False)

            book.save()

            # Парсим главы
            self._parse_fb2_chapters(book, book_data["chapters"])

            serializer = BookDetailSerializer(book, context={"request": request})
            return Response(
                {"message": "Книга успешно загружена", "book": serializer.data},
                status=status.HTTP_201_CREATED,
            )

        except ValueError as e:
            # Специфичные ошибки парсинга FB2
            logger.error(f"Ошибка парсинга FB2 файла: {str(e)}")
            return Response(
                {"error": f"Ошибка формата файла: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            # Общие ошибки
            logger.error(f"Ошибка при загрузке FB2 файла: {str(e)}")
            return Response(
                {"error": f"Ошибка при обработке файла: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

    @action(detail=True, methods=["get"])
    def chapters(self, request, pk=None):
        """Получить все главы книги"""
        book = self.get_object()
        chapters = book.chapters.all()
        serializer = ChapterListSerializer(chapters, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=["get"])
    def chapter_content(self, request, pk=None):
        """Получить содержимое конкретной главы"""
        book = self.get_object()
        chapter_order = request.query_params.get("chapter", 1)

        try:
            chapter = book.chapters.get(order=chapter_order)
            serializer = ChapterDetailSerializer(chapter)
            return Response(
                {"chapter": serializer.data, "total_chapters": book.chapters.count()}
            )
        except Chapter.DoesNotExist:
            return Response({"error": "Глава не найдена"}, status=404)

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
        language = find_text(title_info, "lang", "ru")

        return {
            "title": title,
            "description": description,
            "additional_fields": {
                "authors": ", ".join(authors) if authors else "",
                "genres": genres,
                "language": language,
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

        for idx, section in enumerate(sections, 1):
            chapter_data = self._process_fb2_section(section, namespace, idx)
            if chapter_data:
                chapters.append(chapter_data)

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


class FlashCardViewSet(viewsets.ModelViewSet):
    serializer_class = FlashCardSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return FlashCard.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=False, methods=["get"])
    def review_session(self, request):
        """
        Возвращает карточки для изучения
        """
        limit = int(request.query_params.get("limit", 10))
        cards = FlashCard.get_cards_for_review(request.user, limit)
        serializer = self.get_serializer(cards, many=True)

        return Response(
            {
                "cards": serializer.data,
                "count": len(cards),
                "message": f"Найдено {len(cards)} карточек для изучения",
            }
        )

    @action(detail=True, methods=["post"])
    def submit_answer(self, request, pk=None):
        """
        Обрабатывает ответ пользователя на карточку
        Ожидает: {"quality": 1-4}
        1 - Снова показать (неправильно)
        2 - Сложно (правильно, но с трудом)
        3 - Хорошо (правильно)
        4 - Легко (очень легко)
        """
        card = self.get_object()
        quality = int(request.data.get("quality"))

        if not quality or quality not in [1, 2, 3, 4]:
            return Response(
                {"error": "Quality должно быть от 1 до 4"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Обновляем данные карточки
        card.update_review_data(quality)

        # Определяем сообщение для пользователя
        messages = {
            1: "Карточка будет показана снова. Не сдавайтесь!",
            2: "Хорошо! Карточка будет показана через день.",
            3: f"Отлично! Следующий показ через {card.interval} дн.",
            4: f"Превосходно! Следующий показ через {card.interval} дн.",
        }

        return Response(
            {
                "message": messages[quality],
                "next_review": card.next_review,
                "interval": card.interval,
                "status": card.get_status_display(),
                "repetitions": card.repetitions,
            }
        )

    @action(detail=False, methods=["get"])
    def stats(self, request):
        """
        Возвращает статистику изучения
        """
        stats = FlashCard.get_review_stats(request.user)
        return Response(stats)

    @action(detail=False, methods=["get"])
    def due_today(self, request):
        """
        Возвращает количество карточек на сегодня
        """
        now = timezone.now()
        due_count = FlashCard.objects.filter(
            user=request.user,
            next_review__lte=now,
            status__in=[FlashCard.STATUS_TO_LEARN, FlashCard.STATUS_KNOWN],
        ).count()

        return Response(
            {
                "due_today": due_count,
                "message": f"У вас {due_count} карточек для изучения сегодня",
            }
        )

    @action(detail=False, methods=["post"])
    def reset_progress(self, request):
        """
        Сбрасывает прогресс всех карточек пользователя
        """
        cards_updated = FlashCard.objects.filter(user=request.user).update(
            status=FlashCard.STATUS_TO_LEARN,
            ease_factor=2.5,
            interval=1,
            repetitions=0,
            next_review=timezone.now(),
            last_reviewed=None,
        )

        return Response(
            {
                "message": f"Прогресс сброшен для {cards_updated} карточек",
                "cards_reset": cards_updated,
            }
        )


class UserProfileViewSet(
    viewsets.GenericViewSet,
    viewsets.mixins.RetrieveModelMixin,
    viewsets.mixins.UpdateModelMixin,
):
    serializer_class = UserProfileSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        return self.request.user.profile


class DictionaryEntryViewSet(viewsets.ModelViewSet):
    serializer_class = DictionaryEntrySerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return DictionaryEntry.objects.filter(user=self.request.user)

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class ConversationViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Conversation.objects.filter(user=self.request.user)

    def get_serializer_class(self):
        if self.action == "list":
            return ConversationListSerializer
        else:
            return ConversationSerializer

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    @action(detail=True, methods=["post"])
    def send_message(self, request, pk=None):

        conversation = self.get_object()
        user_message = request.data.get("message", "").strip()

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

            messages = list(conversation.messages.all())

            ai_service = AITeacherService()

            ai_response = ai_service.generate_response(
                user_profile, messages[:-1], user_message
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
        except Exception as e:
            return Response(
                {"error": f"Произошла ошибка: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class TranslateView(generics.CreateAPIView):
    serializer_class = TranslationRequestSerializer
    permission_classes = [IsAuthenticated]
    throttle_classes = [TranslationThrottle, UserRateThrottle]

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            translation_service = TranslationService(user=request.user)
            result = translation_service.translate(**serializer.validated_data)

            response_serializer = TranslationResponseSerializer(data=result)
            response_serializer.is_valid(raise_exception=True)

            return Response(response_serializer.data, status=status.HTTP_200_OK)

        except TranslationServiceError as e:
            logger.error(f"Translation service error: {e}")
            return Response(
                {"success": False, "error": str(e)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
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
    filterset_fields = ["translation_service", "target_language", "source_language"]
    search_fields = ["original_text", "translated_text"]
    ordering_fields = ["created_at"]
    ordering = ["-created_at"]

    def get_queryset(self):
        return Translation.objects.filter(user=self.request.user).select_related()


class TranslationDetailView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = TranslationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Translation.objects.filter(user=self.request.user)
