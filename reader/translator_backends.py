from abc import ABC, abstractmethod
import requests 
import time
from django.conf import settings
from .exceptions import TranslationServiceError
import logging
from .constants import CHATGPT_TRANSLATION_PROMPT_TEMPLATE

logger = logging.getLogger(__name__)

class BaseTranslator(ABC):
    
    def __init__(self):
        self.session = requests.Session()
    
    @abstractmethod
    def translate(self, text, target_language, source_language='auto', context=''):
        pass
    
    def _make_request(self, url, data, headers=None):
        try:
            response = self.session.post(url, json=data, headers=headers, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {e}")
            raise TranslationServiceError(f"Ошибка запроса к сервису: {str(e)}")


class DeepLTranslator(BaseTranslator):
    
    def __init__(self):
        super().__init__()
        self.api_key = getattr(settings, 'DEEPL_API_KEY', None)
        self.base_url = getattr(settings, 'DEEPL_BASE_URL', None)
        
        if not self.api_key:
            logger.warning("DeepL API key not configured")
    
    def translate(self, text, target_language, source_language='auto', context=''):
        """Перевод через DeepL API"""
        if not self.api_key:
            raise TranslationServiceError("DeepL API ключ не настроен")
        
        data = {
            'text': [text],
            'target_lang': target_language.upper(),
            'auth_key': self.api_key,
            'preserve_formatting': True,
            'formality': 'default'
        }
        
        if source_language != 'auto':
            data['source_lang'] = source_language.upper()
        
        if context:
            data['context'] = context
        
        start_time = time.time()
        try:
            response_data = self._make_request(self.base_url, data)
            
            if not response_data.get('translations'):
                raise TranslationServiceError("Пустой ответ от DeepL")
            
            elapsed_ms = round((time.time() - start_time) * 1000, 2)

            translation = response_data['translations'][0]
            
            return {
                'success': True,
                'translated_text': translation['text'],
                'detected_language': translation.get('detected_source_language', source_language).lower(),
                'service': 'deepl',
                'confidence': 0.95,
                'processing_time_ms': elapsed_ms
            }
            
        except Exception as e:
            logger.error(f"DeepL translation failed: {e}")
            raise TranslationServiceError(f"Ошибка DeepL: {str(e)}")

        

class ChatGPTTranslator(BaseTranslator):
    def __init__(self):
        super().__init__()
        self.api_key = getattr(settings, "CHATGPT_API_KEY", None)
        self.model = "gpt-4o"
        self.base_url = getattr(settings, "OPENAI_BASE_URL", None)

    def translate(self, text, target_language, source_language='auto', context=''):
        if not self.api_key:
            raise TranslationServiceError("ChatGPT ключ не найден")

        system_prompt = CHATGPT_TRANSLATION_PROMPT_TEMPLATE.format(source_language=source_language.upper() if source_language != 'auto' else 'любого языка',
        target_language=target_language.upper())
        user_content = f"{text}\n\n{context}" if context else text
        
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content}
        ]

        data = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 5000
        }
            
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        start_time = time.time()
        try:
            response_data = self._make_request(self.base_url, data, headers=headers)

            elapsed_ms = round((time.time() - start_time) * 1000, 2)
            result = response_data['choices'][0]['message']['content']
            return {
                'success': True,
                'translated_text': result.strip(),
                'detected_language': source_language if source_language != 'auto' else 'unknown',
                'service': 'chatgpt',
                'confidence': 0.9,
                'processing_time_ms': elapsed_ms
            }
        except Exception as e:
            logger.error(f"ChatGPT translation failed: {e}")
            raise TranslationServiceError(f"Ошибка ChatGPT: {str(e)}")
