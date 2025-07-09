from django.core.cache import cache
from django.utils import timezone
from django.db import transaction
from ..models import Translation
from ..translator_backends import DeepLTranslator, ChatGPTTranslator
from ..exceptions import TranslationServiceError
import hashlib
import time
import logging

logger = logging.getLogger(__name__)

class TranslationService:
    """Сервис для управления переводами"""
    
    TRANSLATORS = {
        'deepl': DeepLTranslator,
        'chatgpt': ChatGPTTranslator,
    }
    
    def __init__(self, user=None):
        self.user = user
    
    def translate(self, text, target_language, source_language='auto', 
                 context='', service='auto'):
        """Выполняет перевод с кэшированием и сохранением в БД"""
        start_time = time.time()
        
        cache_key = self._generate_cache_key(
            text, target_language, source_language, service
        )
        
        cached_result = cache.get(cache_key)
        if cached_result:
            cached_result.update({
                'cached': True,
                'processing_time_ms': round((time.time() - start_time) * 1000, 2),
                'timestamp': timezone.now()
            })
            return cached_result
        
        db_result = self._get_from_database(text, target_language, service)
        if db_result:
            result = self._format_db_result(db_result, start_time)
            cache.set(cache_key, result, timeout=3600)
            return result
        
        try:
            translator = self._get_translator(service)
            translation_result = translator.translate(
                text, target_language, source_language, context
            )
            
            with transaction.atomic():
                translation_obj = self._save_translation(
                    text, translation_result, target_language, context
                )
            
            result = self._format_translation_result(
                translation_result, text, target_language, start_time
            )
            
            cache.set(cache_key, result, timeout=3600)
            
            return result
            
        except Exception as e:
            logger.error(f"Translation failed: {e}")
            raise TranslationServiceError(f"Ошибка перевода: {str(e)}")
    
    def _generate_cache_key(self, text, target_lang, source_lang, service):
        key_string = f"{text}_{target_lang}_{source_lang}_{service}"
        return f"translation:{hashlib.md5(key_string.encode()).hexdigest()}"
    
    def _get_from_database(self, text, target_language, service):
        try:
            return Translation.objects.filter(
                user=self.user,
                original_text=text,
                target_language=target_language,
                translator_service=service if service != 'auto' else 'deepl'
            ).first()
        except Exception:
            return None
    
    def _get_translator(self, service):       
        translator_class = self.TRANSLATORS.get(service)
        if not translator_class:
            raise TranslationServiceError(f"Неподдерживаемый сервис: {service}")
        
        return translator_class()
    
    def _save_translation(self, text, translation_result, target_language, context):
        return Translation.objects.create(
            user=self.user,
            original_text=text,
            translated_text=translation_result['translated_text'],
            source_language=translation_result.get('detected_language', 'auto'),
            target_language=target_language,
            translator_service=translation_result['service'],
            context=context,
            confidence=translation_result.get('confidence'),
            processing_time_ms=translation_result.get('processing_time_ms')
        )
    
    def _format_db_result(self, translation_obj, start_time):
        return {
            'success': True,
            'original_text': translation_obj.original_text,
            'translated_text': translation_obj.translated_text,
            'source_language': translation_obj.source_language,
            'target_language': translation_obj.target_language,
            'service': translation_obj.translator_service,
            'cached': True,
            'timestamp': timezone.now(),
            'processing_time_ms': round((time.time() - start_time) * 1000, 2)
        }
    
    def _format_translation_result(self, translation_result, text, target_language, start_time):
        return {
            'success': True,
            'original_text': text,
            'translated_text': translation_result['translated_text'],
            'source_language': translation_result.get('detected_language', 'auto'),
            'target_language': target_language,
            'service': translation_result['service'],
            'confidence': translation_result.get('confidence'),
            'cached': False,
            'timestamp': timezone.now(),
            'processing_time_ms': round((time.time() - start_time) * 1000, 2)
        }
        

