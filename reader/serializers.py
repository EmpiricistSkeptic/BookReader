from rest_framework import serializers
from .models import (
    Book,
    FlashCard,
    UserProfile,
    DictionaryEntry,
    Chapter,
    Message,
    Conversation,
    Translation,
    UserBookProgress,
    DictionaryCategory,
)
from .constants import (
    SUPPORTED_LANGUAGES,
    TRANSLATION_SERVICES,
    LANGUAGE_NAMES,
    SERVICE_NAMES,
)
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _
from django.contrib.auth.models import User
from django.contrib.auth import authenticate
from rest_framework_simplejwt.tokens import RefreshToken


class SuggestionRequestSerializer(serializers.Serializer):
    word = serializers.CharField(min_length=1)

    def validate_word(self, value):
        if not value:
            raise serializers.ValidationError(_("Слово не может быть пустым"))

        if not any(c.isalnum() for c in value):
            raise serializers.ValidationError(
                _("Слово должно содержать хотя бы несколько символов")
            )
        return value


class SuggestionResponseSerializer(serializers.Serializer):
    word = serializers.CharField(
        help_text="Оригинальное слово, для которого были сгенерированы предложения."
    )
    translation = serializers.CharField(
        help_text="Основной перевод слова, полученный от сервиса."
    )
    alternatives = serializers.ListField(
        child=serializers.CharField(),
        help_text="Список альтернативных переводов для оригинального слова.",
    )
    examples = serializers.ListField(
        child=serializers.CharField(),
        help_text="Список предложений-примеров, использующих слово и его перевод.",
    )


class UserBookProgressSerializer(serializers.Serializer):
    chapter_order = serializers.IntegerField(min_value=1)
    last_read_page = serializers.IntegerField(min_value=1)

    def validate(self, data):
        request = self.context.get("request")
        book = self.context.get("book")

        chapter_order = data.get("chapter_order")
        last_read_page = data.get("last_read_page")
        try:
            chapter = book.chapters.get(order=chapter_order)
        except Chapter.DoesNotExist:
            raise serializers.ValidationError(_("Глава не найдена."))

        if chapter.total_pages is None:
            raise serializers.ValidationError(_("У главы не указано total_pages."))

        if last_read_page > chapter.total_pages:
            raise serializers.ValidationError(
                _(
                    f"Страница {last_read_page} выходит за пределы главы (максимум {chapter.total_pages})."
                )
            )
        data["chapter"] = chapter
        return data


class ChapterListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Chapter
        fields = ["id", "title", "order"]


class ChapterDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = Chapter
        fields = ["id", "title", "content", "order", "total_pages"]


class BookListSerializer(serializers.ModelSerializer):
    chapter_count = serializers.SerializerMethodField()
    cover_url = serializers.SerializerMethodField()
    user_progress = serializers.SerializerMethodField()

    class Meta:
        model = Book
        fields = [
            "id",
            "title",
            "description",
            "cover_url",
            "book_format",
            "authors",
            "genres",
            "language",
            "file_size",
            "uploaded_at",
            "chapter_count",
            "user_progress",
        ]

    def get_chapter_count(self, obj):
        return obj.chapters.count() if hasattr(obj, "chapters") else 0

    def get_cover_url(self, obj):
        if obj.cover:
            request = self.context.get("request")
            if request:
                return request.build_absolute_uri(obj.cover.url)
        return None

    def get_user_progress(self, obj):
        request = self.context.get("request")

        if not request or not request.user.is_authenticated:
            return None

        user = request.user

        try:
            progress = UserBookProgress.objects.select_related("chapter").get(
                user=user, book=obj
            )

            if not progress.chapter:
                return None

            total_pages = progress.chapter.total_pages
            if not total_pages or total_pages <= 0:
                return {
                    "last_read_chapter_order": progress.chapter.order,
                    "last_read_page": progress.last_read_page,
                    "progress_percentage": 0,
                }

            last_read_page = progress.last_read_page
            percentage = (last_read_page / total_pages) * 100

            return {
                "last_read_chapter_order": progress.chapter.order,
                "last_read_page": last_read_page,
                "progress_percentage": round(percentage, 2),
            }

        except UserBookProgress.DoesNotExist:
            return None


class BookDetailSerializer(serializers.ModelSerializer):
    chapters = ChapterListSerializer(many=True, read_only=True)
    chapter_count = serializers.SerializerMethodField()
    cover_url = serializers.SerializerMethodField()
    user_name = serializers.CharField(source="user.username", read_only=True)
    user_progress = serializers.SerializerMethodField()

    class Meta:
        model = Book
        fields = [
            "id",
            "user",
            "user_name",
            "title",
            "description",
            "cover_url",
            "book_format",
            "file",
            "authors",
            "genres",
            "language",
            "file_size",
            "uploaded_at",
            "chapters",
            "chapter_count",
            "user_progress",
        ]

    def get_chapter_count(self, obj):
        return obj.chapters.count() if hasattr(obj, "chapters") else 0

    def get_cover_url(self, obj):
        if obj.cover:
            request = self.context.get("request")
            if request:
                return request.build_absolute_uri(obj.cover.url)
        return None

    def get_user_progress(self, obj):
        request = self.context.get("request")

        if not request or not request.user.is_authenticated:
            return None

        user = request.user

        try:
            progress = UserBookProgress.objects.select_related("chapter").get(
                user=user, book=obj
            )

            if not progress.chapter:
                return None

            total_pages = progress.chapter.total_pages
            if not total_pages or total_pages <= 0:
                return {
                    "last_read_chapter_order": progress.chapter.order,
                    "last_read_page": progress.last_read_page,
                    "progress_percentage": 0,
                }

            last_read_page = progress.last_read_page
            percentage = (last_read_page / total_pages) * 100

            return {
                "last_read_chapter_order": progress.chapter.order,
                "last_read_page": last_read_page,
                "progress_percentage": round(percentage, 2),
            }

        except UserBookProgress.DoesNotExist:
            return None


class BookCreateUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Book
        fields = [
            "title",
            "description",
            "cover",
            "book_format",
            "authors",
            "genres",
            "language",
        ]

    def validate_book_format(self, value):
        valid_formats = ["FB2", "EPUB"]
        if value not in valid_formats:
            raise serializers.ValidationError(
                f"Поддерживаемые форматы: {', '.join(valid_formats)}"
            )
        return value


class BookUploadSerializer(serializers.Serializer):
    file = serializers.FileField()

    def validate_file(self, value):
        allowed_extensions = (".fb2", ".epub", ".zip")
        if not value.name.lower().endswith(allowed_extensions):
            raise serializers.ValidationError(
                "Поддерживаются только форматы FB2, EPUB и ZIP."
            )

        if value.size > 50 * 1024 * 1024:  # 50MB
            raise serializers.ValidationError("Размер файла не должен превышать 50MB.")

        return value


class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = [
            "native_language",
            "language_to_learn",
            "current_level",
            "google_id",
            "avatar_url",
            "is_google_user",
            "reading_font_size",
            "reading_theme",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["created_at", "updated_at"]


class UserSerializer(serializers.ModelSerializer):
    profile = UserProfileSerializer(read_only=True)

    class Meta:
        model = User
        fields = ["id", "username", "email", "first_name", "last_name", "profile"]
        read_only_fields = ["id"]


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=6)

    class Meta:
        model = User
        fields = ["email", "password", "username", "first_name", "last_name"]

    def create(self, validated_data):
        user = User.objects.create_user(
            email=validated_data["email"],
            username=validated_data.get("username", validated_data["email"]),
            password=validated_data["password"],
            first_name=validated_data.get("first_name", ""),
            last_name=validated_data.get("last_name", ""),
        )
        return user


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField()
    password = serializers.CharField(write_only=True)

    def validate(self, data):
        email = data.get("email")
        password = data.get("password")
        user = None

        if email and password:
            try:
                user_obj = User.objects.get(email=email)
                user = authenticate(username=user_obj.username, password=password)
            except User.DoesNotExist:
                raise serializers.ValidationError("Неверный email или пароль.")

            if not user:
                raise serializers.ValidationError("Неверный email или пароль.")
        else:
            raise serializers.ValidationError("Email и пароль обязательны для входа.")

        data["user"] = user
        return data


class GoogleAuthSerializer(serializers.Serializer):
    id_token = serializers.CharField(required=True)

    def validate_id_token(self, value):
        if not value:
            raise serializers.ValidationError("ID token is required!")
        return value


class FlashCardSerializer(serializers.ModelSerializer):
    class Meta:
        model = FlashCard
        fields = [
            "id",
            "word",
            "translation",
            "example",
            "image",
            "is_learning",
            "learning_step",
            "ease_factor",
            "interval",
            "repetitions",
            "next_review",
            "last_reviewed",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "created_at",
            "updated_at",
            "is_learning",
            "learning_step",
            "ease_factor",
            "interval",
            "repetitions",
            "next_review",
            "last_reviewed",
        ]


class DictionaryCategorySerializer(serializers.ModelSerializer):
    """
    Сериализатор для модели Категории.
    Представляет категорию в компактном виде, только самое нужное для фронтенда.
    """

    class Meta:
        model = DictionaryCategory
        fields = ["name", "slug"]


class DictionaryEntrySerializer(serializers.ModelSerializer):
    """
    Основной сериализатор для модели Словарной статьи.
    Использует вложенный DictionaryCategorySerializer для красивого отображения категорий.
    """

    # Указываем, что поле 'categories' должно быть обработано с помощью
    # DictionaryCategorySerializer. many=True, так как у одного слова может
    # быть много категорий.
    categories = DictionaryCategorySerializer(many=True, read_only=True)

    class Meta:
        model = DictionaryEntry
        # Явно перечисляем поля, которые нужны фронтенду для отображения карточки.
        # Исключаем служебные поля вроде raw, hits, created_at.
        fields = [
            "id",
            "word",
            "transcription",
            "definition",
            "level",
            "categories",
        ]


class DictionaryTranslationResponseSerializer(serializers.Serializer):
    """
    Сериализатор для ФОРМАТИРОВАНИЯ ответа с переводом.
    Также не связан с моделью. Упаковывает результат перевода в JSON.
    """

    translation = serializers.CharField(read_only=True)


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = ["id", "role", "content", "timestamp"]


class ConversationSerializer(serializers.ModelSerializer):
    messages = MessageSerializer(many=True, read_only=True)
    messages_count = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            "id",
            "title",
            "created_at",
            "updated_at",
            "messages",
            "messages_count",
        ]

    def get_messages_count(self, obj):
        return obj.messages.count()


class ConversationListSerializer(serializers.ModelSerializer):
    messages_count = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = [
            "id",
            "title",
            "created_at",
            "updated_at",
            "messages_count",
            "last_message",
        ]

    def get_messages_count(self, obj):
        return obj.messages.count()

    def get_last_message(self, obj):
        last_msg = obj.messages.last()
        return MessageSerializer(last_msg).data if last_msg else None


class TranslationRequestSerializer(serializers.Serializer):
    """Сериализатор для запроса перевода"""

    text = serializers.CharField(
        max_length=5000,
        min_length=1,
        error_messages={
            "required": _("Текст для перевода обязателен"),
            "blank": _("Текст не может быть пустым"),
            "max_length": _("Текст слишком длинный (максимум 5000 символов)"),
        },
    )
    book = serializers.PrimaryKeyRelatedField(
        queryset=Book.objects.all(), required=True
    )
    context = serializers.CharField(
        required=False,
        allow_blank=True,
        max_length=1000,
        help_text=_("Контекст для более точного перевода"),
    )
    service = serializers.ChoiceField(
        choices=TRANSLATION_SERVICES, help_text=_("Сервис для перевода")
    )

    def validate_text(self, value):
        """Дополнительная валидация текста"""
        value = value.strip()

        if not value:
            raise serializers.ValidationError(_("Текст не может быть пустым"))

        if not any(c.isalnum() for c in value):
            raise serializers.ValidationError(
                _("Текст должен содержать хотя бы одну букву или цифру")
            )

        return value

    def validate(self, attrs):
        book = attrs.get("book")
        target_language = self.context.get("target_language")
        if not target_language:
            raise serializers.ValidationError(
                "Не удалось определить целевой язык (target_language)."
            )

        source_language = book.language
        attrs["source_language"] = source_language
        attrs["target_language"] = target_language

        if source_language == target_language:
            raise serializers.ValidationError(
                f"Исходный язык книги ({source_language}) совпадает с вашим языком для изучения ({target_language})."
            )

        return attrs


class TranslationResponseSerializer(serializers.Serializer):
    """Сериализатор для ответа с переводом"""

    success = serializers.BooleanField(read_only=True)
    original_text = serializers.CharField(read_only=True)
    translated_text = serializers.CharField(read_only=True)
    alternatives = serializers.ListField(
        child=serializers.CharField(), required=False, read_only=True
    )
    source_language = serializers.CharField(read_only=True)
    target_language = serializers.CharField(read_only=True)
    service = serializers.CharField(read_only=True)
    confidence = serializers.FloatField(required=False, allow_null=True, read_only=True)
    cached = serializers.BooleanField(default=False, read_only=True)
    timestamp = serializers.DateTimeField(read_only=True)
    processing_time_ms = serializers.FloatField(read_only=True)

    def to_representation(self, instance):
        """Кастомная сериализация ответа"""
        data = super().to_representation(instance)

        data["source_language_name"] = LANGUAGE_NAMES.get(
            data.get("source_language", ""), data.get("source_language", "")
        )
        data["target_language_name"] = LANGUAGE_NAMES.get(
            data.get("target_language", ""), data.get("target_language", "")
        )
        data["service_name"] = SERVICE_NAMES.get(
            data.get("service", ""), data.get("service", "")
        )

        if data.get("confidence"):
            data["confidence_percent"] = round(data["confidence"] * 100, 1)

        return data


class TranslationSerializer(serializers.ModelSerializer):
    """Сериализатор для модели Translation"""

    source_language_name = serializers.SerializerMethodField()
    target_language_name = serializers.SerializerMethodField()
    service_name = serializers.SerializerMethodField()

    class Meta:
        model = Translation
        fields = [
            "id",
            "original_text",
            "translated_text",
            "source_language",
            "target_language",
            "source_language_name",
            "target_language_name",
            "translator_service",
            "service_name",
            "context",
            "created_at",
            "updated_at",
            "alternatives",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_source_language_name(self, obj):
        return LANGUAGE_NAMES.get(obj.source_language, obj.source_language)

    def get_target_language_name(self, obj):
        return LANGUAGE_NAMES.get(obj.target_language, obj.target_language)

    def get_service_name(self, obj):
        return SERVICE_NAMES.get(obj.translator_service, obj.translator_service)
