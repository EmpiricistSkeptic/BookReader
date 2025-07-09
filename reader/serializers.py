from rest_framework import serializers
from .models import Book, FlashCard, UserProfile, DictionaryEntry, Chapter, Message, Conversation, Translation
from .constants import SUPPORTED_LANGUAGES, TRANSLATION_SERVICES, LANGUAGE_NAMES, SERVICE_NAMES 
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils.translation import gettext_lazy as _
from django.contrib.auth.models import User


class ChapterListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Chapter
        fields = ['id', 'title', 'order']


class ChapterDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = Chapter 
        fields = ['id', 'title', 'content', 'order']


class BookListSerializer(serializers.ModelSerializer):
    chapter_count = serializers.SerializerMethodField()
    cover_url = serializers.SerializerMethodField()

    class Meta:
        model = Book
        fields = ['id', 'title', 'description', 'cover', 'cover_url', 'book_format', 'authors', 'genres', 'language', 'file_size', 'uploaded_at', 'chapter_count']


    def get_chapter_count(self, obj):
        return obj.chapters.count() if hasattr(obj, 'chapters') else 0

    def get_cover_url(self, obj):
        if obj.cover:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.cover.url)
        return None


class BookDetailSerializer(serializers.ModelSerializer):
    chapters = ChapterListSerializer(many=True, read_only=True)
    chapter_count = serializers.SerializerMethodField()
    cover_url = serializers.SerializerMethodField()
    user_name = serializers.CharField(source='user.username', read_only=True)

    class Meta:
        model = Book
        fields = ['id',
            'user',
            'user_name',
            'title', 
            'description', 
            'cover',
            'cover_url',
            'book_format', 
            'file',
            'authors', 
            'genres',
            'language',
            'file_size',
            'uploaded_at',
            'chapters',
            'chapter_count']


    def get_chapter_count(self, obj):
        return obj.chapters.count() if hasattr(obj, 'chapters') else 0

    def get_cover_url(self, obj):
        if obj.cover:
            request = self.context.get('request')
            if request:
                return request.build_absolute_uri(obj.cover.url)
        return None


class BookCreateUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Book 
        fields = ['title', 
            'description', 
            'cover',
            'book_format',
            'authors', 
            'genres',
            'language']

    def validate_book_format(self, value):
        valid_formats = ['FB2', 'EPUB']
        if value not in valid_formats:
            raise serializers.ValidationError(f"Поддерживаемые форматы: {', '.join(valid_formats)}")
        return value


class BookUploadSerializer(serializers.ModelSerializer):
    file = serializers.FileField()

    def validate_file(self, value):
        if not value.name.lower().endswith(('.fb2', '.epub')):
            raise serializers.ValidationError("Поддерживаются только файлы FB2 и EPUB")


        if value.size > 50 * 1024 * 1024:
            raise serializers.ValidationError("Размер файла не должен превышать 50MB")
        return value



class UserProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserProfile
        fields = ['native_language', 'language_to_learn', 'current_level', 'google_id', 'avatar_url', 'is_google_user', 'created_at', 'updated_at']
        read_only_fields = ['created_at', 'updated_at']


class UserSerializer(serializers.ModelSerializer):
    profile = UserProfileSerializer(read_only=True)

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'profile']
        read_only_fields = ['id']

                
class GoogleAuthSerializer(serializers.Serializer):
    id_token = serializers.CharField(required=True)

    def validate_id_token(self, value):
        if not value:
            raise serializers.ValidationError('ID token is required!')
        return value


class FlashCardSerializer(serializers.ModelSerializer):
    class Meta:
        model = FlashCard
        fields = ['id', 'word', 'translation', 'example',
                  'image', 'status', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class DictionaryEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = DictionaryEntry
        fields = ['id', 'word', 'translation', 'transcription', 'language']
        read_only_fields = ['id']


class MessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Message
        fields = ['id', 'role', 'content', 'timestamp']


class ConversationSerializer(serializers.ModelSerializer):
    messages = MessageSerializer(many=True, read_only=True)
    messages_count = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = ['id', 'title', 'created_at', 'updated_at', 'messages', 'messages_count']

    def get_messages_count(self, obj):
        return obj.messages.count()


class ConversationListSerializer(serializers.ModelSerializer):
    messages_count = serializers.SerializerMethodField()
    last_message = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = ['id', 'title', 'created_at', 'updated_at', 'messages_count', 'last_message']


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
            'required': _('Текст для перевода обязателен'),
            'blank': _('Текст не может быть пустым'),
            'max_length': _('Текст слишком длинный (максимум 5000 символов)')
        }
    )
    target_language = serializers.ChoiceField(
        choices=SUPPORTED_LANGUAGES,
        default='ru',
        help_text=_('Целевой язык перевода (по умолчанию: ru)')
    )
    source_language = serializers.ChoiceField(
        choices=[('auto', 'auto')] + SUPPORTED_LANGUAGES, 
        default='auto',
        help_text=_('Исходный язык (auto для автоопределения)')
    )
    context = serializers.CharField(
        required=False, 
        allow_blank=True,
        max_length=1000,
        help_text=_('Контекст для более точного перевода')
    )
    service = serializers.ChoiceField(
        choices=TRANSLATION_SERVICES,
        default='auto',
        help_text=_('Сервис для перевода')
    )

    def validate_text(self, value):
        """Дополнительная валидация текста"""
        value = value.strip()

        if not value:
            raise serializers.ValidationError(
                _('Текст не может быть пустым')
            )
        
        if not any(c.isalnum() for c in value):
            raise serializers.ValidationError(
                _('Текст должен содержать хотя бы одну букву или цифру')
            )
        
        return value

    def validate(self, attrs):
        if attrs['source_language'] == attrs['target_language'] and attrs['source_language'] != 'auto':
            raise serializers.ValidationError(
                _('Исходный и целевой языки не могут быть одинаковыми')
            )
        return attrs

    


class TranslationResponseSerializer(serializers.Serializer):
    """Сериализатор для ответа с переводом"""
    success = serializers.BooleanField(read_only=True)
    original_text = serializers.CharField(read_only=True)
    translated_text = serializers.CharField(read_only=True)
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
        
        data['source_language_name'] = LANGUAGE_NAMES.get(data.get('source_language', ''), data.get('source_language', ''))
        data['target_language_name'] = LANGUAGE_NAMES.get(data.get('target_language', ''), data.get('target_language', ''))
        data['service_name'] = SERVICE_NAMES.get(data.get('service', ''), data.get('service', ''))

        if data.get('confidence'):
            data['confidence_percent'] = round(data['confidence'] * 100, 1)
        
        return data


class TranslationSerializer(serializers.ModelSerializer):
    """Сериализатор для модели Translation"""
    source_language_name = serializers.SerializerMethodField()
    target_language_name = serializers.SerializerMethodField()
    service_name = serializers.SerializerMethodField()
    
    class Meta:
        model = Translation
        fields = [
            'id', 'original_text', 'translated_text',
            'source_language', 'target_language',
            'source_language_name', 'target_language_name',
            'translator_service', 'service_name',
            'context', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def get_source_language_name(self, obj):
        return LANGUAGE_NAMES.get(obj.source_language, obj.source_language)

    def get_target_language_name(self, obj):
        return LANGUAGE_NAMES.get(obj.target_language, obj.target_language)
    
    def get_service_name(self, obj):
        return SERVICE_NAMES.get(obj.translator_service, obj.translator_service)



