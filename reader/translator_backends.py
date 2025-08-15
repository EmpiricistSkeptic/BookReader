from abc import ABC, abstractmethod
import requests
import time
import json
import uuid
from django.conf import settings
from .exceptions import TranslationServiceError
import logging
from .constants import (
    CHATGPT_TRANSLATION_PROMPT_TEMPLATE,
    CHATGPT_EXAMPLES_PROMPT_TEMPLATE,
)

logger = logging.getLogger(__name__)


class BaseTranslator(ABC):

    def __init__(self):
        self.session = requests.Session()

    @abstractmethod
    def translate(self, text, target_language, source_language="auto", context=""):
        pass

    @abstractmethod
    def get_examples(
        self, word: str, translation: str, source_language: str, target_language: str
    ) -> list[str]:
        pass

    @abstractmethod
    def get_alternative_translations(
        self, text: str, target_language: str, source_language: str
    ) -> list[dict]:
        """
        Возвращает список альтернативных переводов для слова или фразы.
        Каждый перевод может содержать доп. информацию (часть речи, уверенность).
        """
        pass

    def _make_request(self, url, data=None, headers=None, params=None):
        """
        Универсальный метод для POST-запросов к сервисам перевода.

        :param url: Полный URL запроса
        :param data: JSON-тело запроса (dict или list)
        :param headers: Заголовки запроса
        :param params: Query-параметры (dict)
        """
        try:
            response = self.session.post(
                url, json=data, headers=headers, params=params, timeout=10
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {e}")
            raise TranslationServiceError(f"Ошибка запроса к сервису: {str(e)}")


class DeepLTranslator(BaseTranslator):

    def __init__(self):
        super().__init__()
        self.api_key = getattr(settings, "DEEPL_API_KEY", None)
        self.base_url = getattr(settings, "DEEPL_BASE_URL", None)

        if not self.api_key:
            logger.warning("DeepL API key not configured")

    def translate(self, text, target_language, source_language="auto", context=""):
        """Перевод через DeepL API"""
        if not self.api_key:
            raise TranslationServiceError("DeepL API ключ не настроен")

        data = {
            "text": [text],
            "target_lang": target_language.upper(),
            "auth_key": self.api_key,
            "preserve_formatting": True,
            "formality": "default",
        }

        if source_language != "auto":
            data["source_lang"] = source_language.upper()

        if context:
            data["context"] = context

        start_time = time.time()
        try:
            response_data = self._make_request(self.base_url, data)

            if not response_data.get("translations"):
                raise TranslationServiceError("Пустой ответ от DeepL")

            elapsed_ms = round((time.time() - start_time) * 1000, 2)

            translation = response_data["translations"][0]

            return {
                "success": True,
                "translated_text": translation["text"],
                "detected_language": translation.get(
                    "detected_source_language", source_language
                ).lower(),
                "service": "deepl",
                "confidence": 0.95,
                "processing_time_ms": elapsed_ms,
            }

        except Exception as e:
            logger.error(f"DeepL translation failed: {e}")
            raise TranslationServiceError(f"Ошибка DeepL: {str(e)}")

    def get_alternative_translations(
        self, text: str, target_language: str, source_language: str
    ) -> list[dict]:
        logger.info(
            "DeepLTranslator не поддерживает получение альтернативных переводов."
        )
        return []

    def get_examples(
        self, word: str, translation: str, source_language: str, target_language: str
    ) -> list[str]:
        logger.info("DeepLTranslator не поддерживает генерацию примеров.")
        return []


class ChatGPTTranslator(BaseTranslator):
    def __init__(self):
        super().__init__()
        self.api_key = getattr(settings, "CHATGPT_API_KEY", None)
        self.model = "gpt-4o"
        self.base_url = getattr(settings, "OPENAI_BASE_URL", None)

    def translate(self, text, target_language, source_language="auto", context=""):
        if not self.api_key:
            raise TranslationServiceError("ChatGPT ключ не найден")

        system_prompt = CHATGPT_TRANSLATION_PROMPT_TEMPLATE.format(
            source_language=(
                source_language.upper() if source_language != "auto" else "любого языка"
            ),
            target_language=target_language.upper(),
        )
        user_content = f"{text}/n/n{context}" if context else text

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        data = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 5000,
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        start_time = time.time()
        try:
            response_data = self._make_request(self.base_url, data, headers=headers)

            elapsed_ms = round((time.time() - start_time) * 1000, 2)
            result = response_data["choices"][0]["message"]["content"]
            return {
                "success": True,
                "translated_text": result.strip(),
                "detected_language": (
                    source_language if source_language != "auto" else "unknown"
                ),
                "service": "chatgpt",
                "confidence": 0.9,
                "processing_time_ms": elapsed_ms,
            }
        except Exception as e:
            logger.error(f"ChatGPT translation failed: {e}")
            raise TranslationServiceError(f"Ошибка ChatGPT: {str(e)}")

    def get_alternative_translations(
        self, text: str, target_language: str, source_language: str
    ) -> list[dict]:
        logger.info(
            "ChatGPTTranslator не поддерживает получение альтернативных переводов."
        )
        return []

    def get_examples(
        self, word: str, translation: str, source_language: str, target_language: str
    ) -> list[str]:

        system_prompt = CHATGPT_EXAMPLES_PROMPT_TEMPLATE.format(
            word=word, translation=translation
        )

        user_context = f"{word}\n\n{translation}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_context},
        ]

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        data = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.3,
            "max_tokens": 5000,
        }

        try:
            response_data = self._make_request(self.base_url, data, headers=headers)
            raw_response_text = response_data["choices"][0]["message"]["content"]
            try:
                examples = json.loads(raw_response_text)
                if not isinstance(examples, list):
                    return []
                return examples
            except json.JSONDecodeError:
                logger.error(
                    (
                        f"ChatGPT вернул невалидный JSON для примеров: {raw_response_text}"
                    )
                )
                return []
        except Exception as e:
            logger.error(f"ChatGPT creating examples failed: {e}")


class MicrosoftTranslator(BaseTranslator):
    def __init__(self):
        super().__init__()
        self.api_key = getattr(settings, "MICROSOFT_TRANSLATOR_KEY", None)
        self.region = getattr(settings, "MICROSOFT_TRANSLATOR_REGION", None)
        self.base_url = getattr(settings, "MICROSOFT_TRANSLATOR_BASE_URL", None)

        if not self.api_key or not self.region:
            logger.warning("Microsoft Translator API key или регион не настроены")

    def translate(
        self, text: str, target_language: str, source_language="auto", context=""
    ):
        if not self.api_key or not self.region:
            raise TranslationServiceError(
                "Microsoft Translator API ключ или регион не настроены"
            )

        headers = {
            "Ocp-Apim-Subscription-Key": self.api_key,
            "Ocp-Apim-Subscription-Region": self.region,
            "Content-type": "application/json",
            "X-ClientTraceId": str(uuid.uuid4()),
        }

        params = {"api-version": "3.0", "to": target_language}
        if source_language != "auto":
            params["from"] = source_language

        body = [{"text": text if not context else f"{text}\n\n{context}"}]

        start_time = time.time()
        try:
            response_data = self._make_request(
                self.base_url, data=body, headers=headers, params=params
            )

            if not response_data or "translations" not in response_data[0]:
                raise TranslationServiceError("Пустой ответ от Microsoft Translator")

            translation_result = response_data[0]["translations"][0]
            detected_lang = (
                response_data[0]
                .get("detectedLanguage", {})
                .get("language", source_language)
            )

            detected_lang_str = detected_lang if detected_lang else "und"
            elapsed_ms = round((time.time() - start_time) * 1000, 2)

            return {
                "success": True,
                "translated_text": translation_result["text"],
                "detected_language": detected_lang_str.lower(),
                "service": "microsoft",
                "confidence": float(
                    response_data[0].get("detectedLanguage", {}).get("score", 0.9)
                ),
                "processing_time_ms": elapsed_ms,
            }
        except Exception as e:
            logger.error(f"Microsoft translation failed: {e}")
            raise TranslationServiceError(f"Ошибка Microsoft Translator: {str(e)}")

    def get_alternative_translations(
        self, text: str, target_language: str, source_language: str
    ) -> list[dict]:
        """
        Получает альтернативные переводы с помощью функции "Поиск по словарю" (Dictionary Lookup).
        """
        if not self.api_key or not self.region:
            raise TranslationServiceError(
                "Microsoft Translator API ключ или регион не настроены"
            )

        if source_language == "auto":
            logger.warning("Для поиска по словарю необходимо указать исходный язык.")
            return []

        dictionary_path = "/dictionary/lookup"
        url = self.base_url.replace("/translate", dictionary_path)

        headers = {
            "Ocp-Apim-Subscription-Key": self.api_key,
            "Ocp-Apim-Subscription-Region": self.region,
            "Content-type": "application/json",
            "X-ClientTraceId": str(uuid.uuid4()),
        }

        params = {"api-version": "3.0", "from": source_language, "to": target_language}

        body = [{"text": text}]

        try:
            response_data = self._make_request(
                url, data=body, headers=headers, params=params
            )
            logger.info(f"ПОЛУЧЕН ОТВЕТ от /dictionary/lookup: {response_data}")

            if not response_data or not response_data[0].get("translations"):
                return []

            alternatives = []
            for translation in response_data[0]["translations"]:
                alternatives.append(
                    {
                        "text": translation.get("displayTarget"),
                        "pos_tag": translation.get(
                            "posTag"
                        ),  # Часть речи (NOUN, VERB и т.д.)
                        "confidence": translation.get("confidence"),
                    }
                )

            return alternatives

        except Exception as e:
            logger.error(f"Microsoft dictionary lookup failed: {e}")
            raise TranslationServiceError(
                f"Ошибка поиска по словарю Microsoft: {str(e)}"
            )

    def get_examples(
    self, word: str, translation: str, source_language: str, target_language: str
) -> list[str]:
        """
        Получает примеры использования пары "слово-перевод" в контексте предложений.
        [ФИНАЛЬНАЯ ДИАГНОСТИЧЕСКАЯ ВЕРСИЯ]
        """
        logger.info("--- [DEBUG EXAMPLES] ВХОД В GET_EXAMPLES ---")
        logger.info(f"[DEBUG EXAMPLES] Ищем примеры для пары: word='{word}', translation='{translation}'")
        logger.info(f"[DEBUG EXAMPLES] Языки: from='{source_language}', to='{target_language}'")

        if not self.api_key or not self.region:
            raise TranslationServiceError("Microsoft Translator API ключ или регион не настроены")

        examples_path = "/dictionary/examples"
        url = self.base_url.replace("/translate", examples_path)
        headers = {
            "Ocp-Apim-Subscription-Key": self.api_key,
            "Ocp-Apim-Subscription-Region": self.region,
            "Content-type": "application/json",
            "X-ClientTraceId": str(uuid.uuid4()),
        }
        params = {"api-version": "3.0", "from": source_language, "to": target_language}
        body = [{"text": word, "translation": translation}]

        logger.info(f"[DEBUG EXAMPLES] Отправляем запрос на URL: {url} с параметрами: {params}")
        logger.info(f"[DEBUG EXAMPLES] Тело запроса: {body}")

        try:
            response_data = self._make_request(url, data=body, headers=headers, params=params)
            
            # САМАЯ ВАЖНАЯ СТРОКА ДЛЯ ДИАГНОСТИКИ
            logger.info(f"[DEBUG EXAMPLES] СЫРОЙ ОТВЕТ ОТ API: {response_data}")

            if not response_data or not response_data[0].get("examples"):
                logger.warning("[DEBUG EXAMPLES] Ответ пуст или не содержит ключ 'examples'. Возвращаем пустой список [].")
                return []

            formatted_examples = []
            for example in response_data[0]["examples"]:
                source_sentence = f"{example['sourcePrefix']}{example['sourceTerm']}{example['sourceSuffix']}"
                target_sentence = f"{example['targetPrefix']}{example['targetTerm']}{example['targetSuffix']}"
                formatted_examples.append(f"{source_sentence} -> {target_sentence}")
            
            logger.info(f"[DEBUG EXAMPLES] Успешно отформатировано {len(formatted_examples)} примеров. Возвращаем результат.")
            return formatted_examples
            
        except Exception as e:
            logger.error(f"[DEBUG EXAMPLES] Ошибка при выполнении _make_request: {e}", exc_info=True)
            raise TranslationServiceError(f"Ошибка получения примеров Microsoft: {str(e)}")
