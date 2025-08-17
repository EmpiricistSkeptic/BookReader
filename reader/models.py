from django.db import models
from django.contrib.auth.models import User
from django.core.validators import (
    MinLengthValidator,
    MinValueValidator,
    MaxValueValidator,
)
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from datetime import timedelta
from django.db.models import Count, Q
from .constants import SUPPORTED_LANGUAGES
from django.utils.text import slugify


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
    avatar_url = models.ImageField(null=True, blank=True)
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


class Conversation(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    title = models.CharField(max_length=200, blank=True)
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


class FlashCard(models.Model):

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="flashcards")
    word = models.CharField(max_length=50)
    translation = models.CharField(max_length=50)
    example = models.TextField(blank=True)
    image = models.ImageField(upload_to="flashcard_images/", blank=True)
    is_learning = models.BooleanField(
        default=True, help_text="Карточка на этапе первоначального изучения?"
    )
    learning_step = models.PositiveIntegerField(
        default=1, help_text="Карточка на этапе первоначального изучения?"
    )

    ease_factor = models.FloatField(default=2.5)  # Фактор легкости (2.5 по умолчанию)
    interval = models.IntegerField(default=1)  # Интервал в днях
    repetitions = models.IntegerField(default=0)  # Количество повторений
    next_review = models.DateTimeField(default=timezone.now)  # Следующий показ
    last_reviewed = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["next_review"]

    def __str__(self):
        return f"{self.word} (Next review: {self.next_review.date()})"

    def update_review_data(self, knows_word: bool):
        """
        Обновляет данные карточки на основе бинарного ответа (знаю/не знаю).
        Реализует фазы обучения и повторения.
        """
        # --- Настраиваемые параметры алгоритма ---
        LEARNING_STEPS_MINUTES = [1, 10]  # Шаги для новых карточек (1 минута, 10 минут)
        GRADUATING_INTERVAL_DAYS = 1  # Первый интервал после выхода из фазы обучения
        RELEARNING_INTERVAL_DAYS = 1  # Интервал для карточек, которые были забыты
        MIN_EASE_FACTOR = 1.3  # Минимальный фактор легкости

        self.last_reviewed = timezone.now()

        if self.is_learning:
            # --- ЛОГИКА ДЛЯ ФАЗЫ ОБУЧЕНИЯ ---
            if knows_word:
                # Пользователь знает слово, двигаем на следующий шаг
                current_step_index = self.learning_step - 1
                if current_step_index < len(LEARNING_STEPS_MINUTES) - 1:
                    # Есть еще шаги в фазе обучения
                    self.learning_step += 1
                    delay_minutes = LEARNING_STEPS_MINUTES[self.learning_step - 1]
                    self.next_review = timezone.now() + timedelta(minutes=delay_minutes)
                else:
                    # Последний шаг обучения пройден, "выпускаем" карточку в фазу повторения
                    self.is_learning = False
                    self.learning_step = 1  # Сбрасываем на всякий случай
                    self.interval = GRADUATING_INTERVAL_DAYS
                    self.next_review = timezone.now() + timedelta(days=self.interval)
            else:
                # Пользователь не знает слово, возвращаем на первый шаг
                self.repetitions = 0  # Сбрасываем счетчик успешных повторений
                self.learning_step = 1
                delay_minutes = LEARNING_STEPS_MINUTES[0]
                self.next_review = timezone.now() + timedelta(minutes=delay_minutes)
        else:
            # --- ЛОГИКА ДЛЯ ФАЗЫ ПОВТОРЕНИЯ ---
            if knows_word:
                # Пользователь знает слово, увеличиваем интервал
                self.repetitions += 1
                new_interval = max(
                    self.interval + 1, int(self.interval * self.ease_factor)
                )
                self.interval = new_interval
                # Немного увеличиваем фактор легкости
                self.ease_factor = max(MIN_EASE_FACTOR, self.ease_factor + 0.15)
                self.next_review = timezone.now() + timedelta(days=self.interval)
            else:
                # Пользователь забыл слово, возвращаем карточку в фазу обучения (relearning)
                self.is_learning = True
                self.repetitions = 0
                self.learning_step = 1  # Возвращаем на первый шаг
                # Значительно уменьшаем фактор легкости, так как слово было забыто
                self.ease_factor = max(MIN_EASE_FACTOR, self.ease_factor - 0.2)
                self.interval = RELEARNING_INTERVAL_DAYS
                delay_minutes = LEARNING_STEPS_MINUTES[0]
                self.next_review = timezone.now() + timedelta(minutes=delay_minutes)

        self.save()

    # --- НОВЫЕ СТАТИЧЕСКИЕ МЕТОДЫ ---
    @classmethod
    def get_review_stats(cls, user: User):
        """Возвращает статистику одним эффективным запросом."""
        today = timezone.localdate(timezone.now())

        stats = cls.objects.filter(user=user).aggregate(
            total_cards=Count("id"),
            # Карточки, которые нужно показать сегодня (или уже просрочены)
            due_today=Count("id", filter=Q(next_review__lte=timezone.now())),
            # Из них: сколько на этапе первоначального заучивания
            learning_now=Count(
                "id", filter=Q(is_learning=True, next_review__lte=timezone.now())
            ),
            # Из них: сколько на этапе долгосрочного повторения
            reviewing=Count(
                "id", filter=Q(is_learning=False, next_review__lte=timezone.now())
            ),
        )
        return stats

    @classmethod
    def reset_all_progress(cls, user: User):
        """Сбрасывает прогресс для всех карточек пользователя."""
        cards_updated = cls.objects.filter(user=user).update(
            is_learning=True,
            learning_step=1,
            ease_factor=2.5,
            interval=1,
            repetitions=0,
            next_review=timezone.now(),
            last_reviewed=None,
        )
        return cards_updated

class DictionaryCategory(models.Model):
    slug = models.SlugField(max_length=120, unique=True)
    name = models.CharField(max_length=200)
    language = models.CharField(max_length=8, default="en")

    def save(self, *args, **kwargs):
        if not self.slug:
            self.slug = slugify(self.name)[:120]
        super().save(*args, **kwargs)

    def __str__(self):
        return self.name

class DictionaryEntry(models.Model):
    CEFR_LEVELS = (
    ("A1","A1"),("A2","A2"),("B1","B1"),("B2","B2"),("C1","C1"),("C2","C2"),
    )
    word = models.CharField(max_length=200, db_index=True)
    transcription = models.CharField(max_length=200, blank=True)
    definition = models.TextField(blank=True)
    level = models.CharField(max_length=2, choices=CEFR_LEVELS, blank=True, null=True)
    categories = models.ManyToManyField(DictionaryCategory, blank=True, related_name="entries")
    raw = models.TextField(blank=True, help_text="Original raw text from source")
    hits = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("word", "level")

    def __str__(self):
        return f"{self.word}" + (f" ({self.level})" if self.level else "")

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
