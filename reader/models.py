from datetime import timedelta

from django.contrib.auth.models import User
from django.core.validators import (
    MaxValueValidator,
    MinLengthValidator,
    MinValueValidator,
)
from django.db import models
from django.db.models import Count, Q
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from .constants import SUPPORTED_LANGUAGES


class Book(models.Model):
    FB2 = "FB2"
    EPUB = "EPUB"
    FORMAT_CHOICES = [
        (FB2, "FB2"),
        (EPUB, "EPUB"),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="books")
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    cover = models.ImageField(upload_to="covers/", blank=True)
    book_format = models.CharField(max_length=4, choices=FORMAT_CHOICES)
    file = models.FileField(upload_to="books/", null=True, blank=True)
    authors = models.TextField(blank=True)
    genres = models.TextField(blank=True)
    language = models.CharField(
        max_length=10, choices=SUPPORTED_LANGUAGES, default="en"
    )
    file_size = models.PositiveIntegerField(null=True, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title


class Chapter(models.Model):
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name="chapters")
    title = models.CharField(max_length=500)
    content = models.TextField()
    total_pages = models.PositiveIntegerField(null=True, blank=True)
    order = models.PositiveIntegerField()

    class Meta:
        ordering = ["order"]
        unique_together = ["book", "order"]


class UserBookProgress(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    book = models.ForeignKey(Book, on_delete=models.CASCADE)
    chapter = models.ForeignKey(Chapter, on_delete=models.SET_NULL, null=True)
    last_read_page = models.PositiveIntegerField(default=1)
    last_read = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ["user", "book"]


class UserProfile(models.Model):
    LANGUAGE_CHOICES = [
        ("ru", "Russian"),
        ("en", "English"),
        ("es", "Spanish"),
        ("fr", "French"),
        ("de", "German"),
        ("zh", "Chinese"),
        ("ja", "Japanese"),
        ("id", "Indonesian"),
    ]

    LEVEL_CHOICES = [
        ("A1", "Beginner"),
        ("A2", "Elementary"),
        ("B1", "Intermediate"),
        ("B2", "Upper Intermediate"),
        ("C1", "Advanced"),
        ("C2", "Proficient"),
    ]

    THEME_CHOICES = [
        ("light", "Светлая"),
        ("sepia", "Сепия"),
        ("dark", "Темная"),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    native_language = models.CharField(max_length=2, choices=LANGUAGE_CHOICES)
    language_to_learn = models.CharField(max_length=2, choices=LANGUAGE_CHOICES)
    current_level = models.CharField(
        max_length=10, choices=LEVEL_CHOICES, help_text="CEFR level, e.g. A1, B2"
    )
    google_id = models.CharField(max_length=100, unique=True, null=True, blank=True)
    avatar = models.ImageField(upload_to="avatars/", null=True, blank=True)
    is_google_user = models.BooleanField(default=False)
    reading_font_size = models.IntegerField(
        default=16, validators=[MinValueValidator(12), MaxValueValidator(28)]
    )
    reading_theme = models.CharField(
        max_length=10, choices=THEME_CHOICES, default="light"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username}'s profile"

class ConversationMode(models.TextChoices):
    DEFAULT = "default", "General Tutor"
    GRAMMAR = "grammar", "Grammar Lab"
    ROLEPLAY = "roleplay", "Role Play"
    CONVERSATION = "conversation", "Conversation Club"
    WRITING = "writing", "Writing Coach"
    VOCABULARY = "vocabulary", "Vocabulary Builder"

class Conversation(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="conversations")
    title = models.CharField(max_length=200, blank=True)
    mode = models.CharField(max_length=30, choices=ConversationMode.choices, default=ConversationMode.DEFAULT)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Conversation {self.id} - {self.user.username}"

    class Meta:
        ordering = ["-updated_at"]


class Message(models.Model):
    ROLE_CHOICES = [("user", "User"), ("assistant", "Assistant")]
    conversation = models.ForeignKey(
        Conversation, on_delete=models.CASCADE, related_name="messages"
    )
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    content = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["timestamp"]

    def __str__(self):
        return f"{self.role}: {self.content[:50]}..."


class Translation(models.Model):
    LANGUAGE_CHOICES = [
        ("ru", "Русский"),
        ("en", "Английский"),
        ("de", "Немецкий"),
        ("fr", "Французский"),
        ("es", "Испанский"),
        ("it", "Итальянский"),
        ("pt", "Португальский"),
        ("pl", "Польский"),
        ("nl", "Голландский"),
        ("ja", "Японский"),
        ("zh", "Китайский"),
        ("ko", "Корейский"),
        ("ar", "Арабский"),
        ("hi", "Хинди"),
        ("tr", "Турецкий"),
        ("uk", "Украинский"),
        ("bg", "Болгарский"),
        ("cs", "Чешский"),
    ]

    TRANSLATOR_CHOICES = [
        ("deepl", "DeepL"),
        ("chatgpt", "ChatGPT"),
        ("microsoft", "Microsoft"),
        ("deepseek", "DeepSeek"),
    ]

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="translations",
        verbose_name=_("Пользователь"),
    )
    original_text = models.TextField(
        max_length=5000,
        validators=[MinLengthValidator(1)],
        verbose_name=_("Исходный текст"),
    )
    translated_text = models.TextField(verbose_name=_("Переведенный текст"))
    alternatives = models.JSONField(
        "Альтернативные переводы", default=list, null=True, blank=True
    )
    source_language = models.CharField(
        max_length=10,
        choices=LANGUAGE_CHOICES,
        verbose_name=_("Исходный язык"),
    )
    target_language = models.CharField(
        max_length=10,
        choices=LANGUAGE_CHOICES,
        verbose_name=_("Целевой язык"),
    )
    translator_service = models.CharField(
        max_length=20, choices=TRANSLATOR_CHOICES, verbose_name=_("Сервис перевода")
    )
    context = models.TextField(
        blank=True, null=True, max_length=1000, verbose_name=_("Контекст")
    )
    confidence = models.FloatField(
        null=True,
        blank=True,
        verbose_name=_("Уверенность перевода"),
        help_text=_("Значение от 0 до 1"),
    )
    processing_time_ms = models.PositiveIntegerField(
        null=True, blank=True, verbose_name=_("Время обработки (мс)")
    )
    created_at = models.DateTimeField(auto_now_add=True, verbose_name=_("Создано"))
    updated_at = models.DateTimeField(auto_now=True, verbose_name=_("Обновлено"))

    class Meta:
        verbose_name = _("Перевод")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["user", "created_at"]),
            models.Index(fields=["user", "target_language"]),
            models.Index(fields=["user", "translator_service"]),
            models.Index(fields=["user", "source_language"], name="tr_user_srclang_idx"),
        ]
        unique_together = [
            "user",
            "original_text",
            "source_language",
            "target_language",
            "translator_service",
        ]

    def __str__(self):
        return f'"{self.original_text[:20]}" ({self.source_language} -> {self.target_language})'

    @property
    def confidence_percent(self):
        if self.confidence is not None:
            return round(self.confidence * 100, 1)
        return None

class WordAnalysis(models.Model):
    translation = models.OneToOneField(
        Translation,
        on_delete=models.CASCADE,
        related_name="word_analysis",
    )

    lemma = models.CharField(max_length=255, blank=True)
    ipa = models.CharField(max_length=255, blank=True)
    part_of_speech = models.CharField(max_length=50, blank=True)

    grammar = models.JSONField(default=dict, blank=True)

    explanation = models.TextField(blank=True)

    synonyms = models.JSONField(default=list, blank=True)

    examples = models.JSONField(default=list, blank=True)

    etymology = models.TextField(blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)






