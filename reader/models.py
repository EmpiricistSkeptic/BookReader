from django.db import models
from django.contrib.auth.models import User
from django.core.validators import MinLengthValidator
from django.utils.translation import gettext_lazy as _
from django.utils import timezone
from datetime import timedelta


class Book(models.Model):
    FB2 = 'FB2'
    EPUB = 'EPUB'
    FORMAT_CHOICES = [
        (FB2, 'FB2'),
        (EPUB, 'EPUB'),
    ]

    user = models.ForeignKey(User,
                             on_delete=models.CASCADE,
                             related_name='books')
    title = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    cover = models.ImageField(upload_to='covers/', blank=True)
    book_format = models.CharField(max_length=4,
                                   choices=FORMAT_CHOICES)
    file = models.FileField(upload_to='books/', null=True, blank=True)
    authors = models.TextField(blank=True)
    genres = models.TextField(blank=True)
    language = models.CharField(max_length=10, default='en')
    file_size = models.PositiveIntegerField(null=True, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.title


class Chapter(models.Model):
    book = models.ForeignKey(Book, on_delete=models.CASCADE, related_name='chapters')
    title = models.CharField(max_length=500)
    content = models.TextField()
    order = models.PositiveIntegerField()

    class Meta:
        ordering = ['order']
        unique_together = ['book', 'order']


class UserProfile(models.Model):
    LANGUAGE_CHOICES = [
        ('ru', 'Russian'),
        ('en', 'English'),
        ('es', 'Spanish'),  
        ('fr', 'French'),
        ('de', 'German'),
        ('zh', 'Chinese'),
        ('ja', 'Japanese'),
        
    ]
    
    LEVEL_CHOICES = [
        ('A1', 'Beginner'),
        ('A2', 'Elementary'),
        ('B1', 'Intermediate'),
        ('B2', 'Upper Intermediate'),
        ('C1', 'Advanced'),
        ('C2', 'Proficient'),
    ]

    user = models.OneToOneField(User,
                                on_delete=models.CASCADE,
                                related_name='profile')
    native_language = models.CharField(max_length=2,
                                       choices=LANGUAGE_CHOICES)
    language_to_learn = models.CharField(max_length=2,
                                         choices=LANGUAGE_CHOICES)
    current_level = models.CharField(max_length=10, choices=LEVEL_CHOICES,
                                     help_text='CEFR level, e.g. A1, B2')
    google_id = models.CharField(max_length=100, unique=True, null=True, blank=True)
    avatar_url = models.ImageField(null=True, blank=True)
    is_google_user = models.BooleanField(default=False)
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
        ordering = ['-updated_at']


class Message(models.Model):
    ROLE_CHOICES = [
        ('user', 'User'),
        ('assistant', 'Assistant')
    ]
    conversation = models.ForeignKey(Conversation, on_delete=models.CASCADE, related_name='messages')
    role = models.CharField(max_length=10, choices=ROLE_CHOICES)
    content = models.TextField()
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['timestamp']

    def __str__(self):
        return f"{self.role}: {self.content[:50]}..."


class FlashCard(models.Model):
    STATUS_TO_LEARN = 'TL'
    STATUS_KNOWN = 'KN'
    STATUS_LEARNED = 'LD'
    STATUS_CHOICES = [
        (STATUS_TO_LEARN, 'To Learn'),
        (STATUS_KNOWN, 'Known'),
        (STATUS_LEARNED, 'Learned'),
    ]

    QUALITY_AGAIN = 1      # Неправильный ответ
    QUALITY_HARD = 2       # Правильный ответ с трудом
    QUALITY_GOOD = 3       # Правильный ответ
    QUALITY_EASY = 4       # Очень легкий ответ


    user = models.ForeignKey(User,
                             on_delete=models.CASCADE,
                             related_name='flashcards')
    word = models.CharField(max_length=50)
    translation = models.CharField(max_length=50)
    example = models.TextField(blank=True)
    image = models.ImageField(upload_to='flashcard_images/',
                              blank=True)
    status = models.CharField(max_length=2,
                              choices=STATUS_CHOICES,
                              default=STATUS_TO_LEARN)

    ease_factor = models.FloatField(default=2.5)  # Фактор легкости (2.5 по умолчанию)
    interval = models.IntegerField(default=1)     # Интервал в днях
    repetitions = models.IntegerField(default=0)  # Количество повторений
    next_review = models.DateTimeField(default=timezone.now)  # Следующий показ
    last_reviewed = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    def __str__(self):
        return f"{self.word} ({self.get_status_display()})"
    
    def update_review_data(self, quality):
        """
        Обновляет данные карточки на основе качества ответа
        quality: 1-4 (1=снова показать, 2=сложно, 3=хорошо, 4=легко)
        """
        self.last_reviewed = timezone.now()
        
        if quality < 3:  
            self.repetitions = 0
            self.interval = 1
            self.status = self.STATUS_TO_LEARN
        else:
            self.repetitions += 1
            
            if self.repetitions == 1:
                self.interval = 1
            elif self.repetitions == 2:
                self.interval = 6
            else:
                self.interval = int(self.interval * self.ease_factor)
            
            # Обновляем фактор легкости
            self.ease_factor = max(1.3, self.ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02)))
            
            if self.repetitions >= 5 and self.ease_factor > 2.0:
                self.status = self.STATUS_LEARNED
            elif self.repetitions >= 2:
                self.status = self.STATUS_KNOWN
        
        self.next_review = timezone.now() + timedelta(days=self.interval)
        self.save()

    
    @classmethod
    def get_cards_for_review(cls, user, limit=10):
        """
        Возвращает карточки для изучения на основе алгоритма
        """
        now = timezone.now()
        
        # Карточки, которые нужно повторить (время пришло)
        due_cards = cls.objects.filter(
            user=user,
            next_review__lte=now,
            status__in=[cls.STATUS_TO_LEARN, cls.STATUS_KNOWN]
        ).order_by('next_review')
        
        # Новые карточки (еще не изучались)
        new_cards = cls.objects.filter(
            user=user,
            status=cls.STATUS_TO_LEARN,
            repetitions=0,
            last_reviewed__isnull=True
        ).order_by('created_at')
        
        cards_list = list(due_cards[:limit//2]) + list(new_cards[:limit//2])
        
        # Если карточек меньше лимита, добираем из любых доступных
        if len(cards_list) < limit:
            remaining = limit - len(cards_list)
            additional_cards = cls.objects.filter(
                user=user,
                status__in=[cls.STATUS_TO_LEARN, cls.STATUS_KNOWN]
            ).exclude(
                id__in=[card.id for card in cards_list]
            ).order_by('?')[:remaining] 
            
            cards_list.extend(additional_cards)
        
        return cards_list[:limit]

    
    @classmethod
    def get_review_stats(cls, user):
        """
        Возвращает статистику изучения для пользователя
        """
        now = timezone.now()
        
        total_cards = cls.objects.filter(user=user).count()
        learned_cards = cls.objects.filter(user=user, status=cls.STATUS_LEARNED).count()
        known_cards = cls.objects.filter(user=user, status=cls.STATUS_KNOWN).count()
        to_learn_cards = cls.objects.filter(user=user, status=cls.STATUS_TO_LEARN).count()
        
        # Карточки на сегодня
        due_today = cls.objects.filter(
            user=user,
            next_review__lte=now,
            status__in=[cls.STATUS_TO_LEARN, cls.STATUS_KNOWN]
        ).count()
        
        return {
            'total_cards': total_cards,
            'learned_cards': learned_cards,
            'known_cards': known_cards,
            'to_learn_cards': to_learn_cards,
            'due_today': due_today,
            'learning_progress': round((learned_cards / total_cards * 100) if total_cards > 0 else 0, 1)
        }

    
class DictionaryEntry(models.Model):
    user = models.ForeignKey(User,
                             on_delete=models.CASCADE,
                             related_name='dictionary_entries')
    word = models.CharField(max_length=50)
    translation = models.CharField(max_length=50)
    transcription = models.CharField(max_length=50,
                                     blank=True)
    language = models.CharField(max_length=2,
                                choices=UserProfile.LANGUAGE_CHOICES)

    def __str__(self):
        return f"{self.word} → {self.translation}"


class Translation(models.Model):
    LANGUAGE_CHOICES = [
        ('ru', 'Русский'), ('en', 'Английский'), ('de', 'Немецкий'),
        ('fr', 'Французский'), ('es', 'Испанский'), ('it', 'Итальянский'),
        ('pt', 'Португальский'), ('pl', 'Польский'), ('nl', 'Голландский'),
        ('ja', 'Японский'), ('zh', 'Китайский'), ('ko', 'Корейский'),
        ('ar', 'Арабский'), ('hi', 'Хинди'), ('tr', 'Турецкий'),
        ('uk', 'Украинский'), ('bg', 'Болгарский'), ('cs', 'Чешский'),
        ('auto', 'Автоопределение')
    ]

    TRANSLATOR_CHOICES = [
        ('deepl', 'DeepL'),
        ('google', 'Google Translate'),
        ('contextil', 'Contextil'),
    ]

    user = models.ForeignKey(
        User, 
        on_delete=models.CASCADE,
        related_name='translations',
        verbose_name=_('Пользователь')
    )
    original_text = models.TextField(  
        max_length=5000,
        validators=[MinLengthValidator(1)],
        verbose_name=_('Исходный текст')
    )
    translated_text = models.TextField(
        verbose_name=_('Переведенный текст')
    )
    source_language = models.CharField(
        max_length=10, 
        choices=LANGUAGE_CHOICES,
        default='auto',
        verbose_name=_('Исходный язык')
    )
    target_language = models.CharField(
        max_length=10, 
        choices=LANGUAGE_CHOICES,
        default='ru',
        verbose_name=_('Целевой язык')
    )
    translator_service = models.CharField(
        max_length=20, 
        choices=TRANSLATOR_CHOICES,
        verbose_name=_('Сервис перевода')
    )
    context = models.TextField(
        blank=True, 
        null=True,
        max_length=1000,
        verbose_name=_('Контекст')
    )
    confidence = models.FloatField(
        null=True, 
        blank=True,
        verbose_name=_('Уверенность перевода'),
        help_text=_('Значение от 0 до 1')
    )
    processing_time_ms = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_('Время обработки (мс)')
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name=_('Создано')
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name=_('Обновлено')
    )

    class Meta:
        verbose_name = _('Перевод')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'created_at']),
            models.Index(fields=['user', 'target_language']),
            models.Index(fields=['user', 'translator_service']),
            models.Index(fields=['original_text', 'target_language', 'translator_service'])
        ]
        unique_together = ['user', 'original_text', 'target_language', 'translator_service']

    def __str__(self):
        return f"{self.original_text[:50]}... -> {self.target_language} ({self.translator_service})"
         
    @property
    def confidence_percent(self):
        if self.confidence is not None:
            return round(self.confidence * 100, 1)
        return None



