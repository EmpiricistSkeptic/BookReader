from django.core.cache import cache
from django.utils import timezone
from django.db import transaction
from ..models import Translation
from ..translator_backends import (
    DeepLTranslator,
    ChatGPTTranslator,
    MicrosoftTranslator,
)
from ..exceptions import TranslationServiceError
import hashlib
import time
import logging

logger = logging.getLogger(__name__)


class TranslationService:
    """Сервис для управления переводами"""

    TRANSLATORS = {
        "deepl": DeepLTranslator,
        "chatgpt": ChatGPTTranslator,
        "microsoft": MicrosoftTranslator,
    }

    def __init__(self, user=None):
        self.user = user

    def translate(
        self, text, target_language, source_language="auto", context="", service="auto"
    ):
        """
        Выполняет перевод с кэшированием, сохранением в БД и получением
        альтернативных переводов для одиночных слов.
        """

        logger.info(
            f"\n\n--- НАЧАЛО ЗАПРОСА НА ПЕРЕВОД ---\n"
            f"Текст: '{text}'\n"
            f"Целевой язык (входящий): '{target_language}'\n"
            f"Исходный язык (входящий): '{source_language}'\n"
            f"Сервис: '{service}'\n"
            f"---------------------------------\n"
        )
        start_time = time.time()

        # ИЗМЕНЕНО: Ключ кэша остается прежним, но теперь он будет хранить более богатый объект
        cache_key = self._generate_cache_key(
            text, target_language, source_language, service
        )

        cached_result = cache.get(cache_key)
        if cached_result:
            # ИЗМЕНЕНО: Добавляем доп. поля и возвращаем кэшированный результат
            cached_result.update(
                {
                    "cached": True,
                    "from_cache": True,  # Добавим флаг для ясности
                    "processing_time_ms": round((time.time() - start_time) * 1000, 2),
                    "timestamp": timezone.now(),
                }
            )
            return cached_result

        # ИЗМЕНЕНО: Поиск в БД также будет возвращать объект с альтернативами, если они сохранены
        db_result = self._get_from_database(
            text, target_language, source_language, service
        )
        if db_result:
            result = self._format_db_result(db_result, start_time)
            cache.set(cache_key, result, timeout=3600)
            return result

        try:
            translator = self._get_translator(service)

            # --- ШАГ 1: Получаем основной перевод ---
            translation_result = translator.translate(
                text, target_language, source_language, context
            )

            # --- НОВЫЙ ШАГ 2: Получаем альтернативные переводы, если это возможно ---
            alternatives = []
            # Проверяем, что это одно слово и язык был определен (важно для Microsoft Translator)
            is_single_word = " " not in text.strip()
            detected_language = translation_result.get("detected_language")
            logger.info(
                f"ПРОВЕРКА 'СТРАЖНИКА': Слово: '{text}'. is_single_word: {is_single_word}. "
                f"translation_result содержит 'detected_language': {'detected_language' in translation_result}. "
                f"Значение detected_language: '{detected_language}'."
            )

            if is_single_word and detected_language and detected_language != "unknown":
                # Безопасно проверяем, поддерживает ли переводчик этот метод
                if hasattr(translator, "get_alternative_translations"):
                    try:
                        # Используем уже определенный язык для точности
                        alts_from_provider = translator.get_alternative_translations(
                            text, target_language, detected_language
                        )
                        # Форматируем в простой список строк для фронтенда
                        alternatives = [
                            alt["text"] for alt in alts_from_provider if alt.get("text")
                        ]
                    except Exception as e:
                        logger.warning(
                            f"Не удалось получить альтернативные переводы для '{text}': {e}"
                        )
                        # Не прерываем основной запрос, просто логируем и продолжаем

            # --- НОВЫЙ ШАГ 3: Сохраняем все вместе ---
            with transaction.atomic():
                translation_obj = self._save_translation(
                    text,
                    translation_result,
                    source_language,
                    target_language,
                    context,
                    alternatives,
                )

            # --- НОВЫЙ ШАГ 4: Форматируем итоговый ответ ---
            result = self._format_translation_result(
                translation_result,
                text,
                source_language,
                target_language,
                start_time,
                alternatives,
            )

            cache.set(cache_key, result, timeout=3600)

            return result

        except Exception as e:
            logger.error(f"Translation failed: {e}")
            raise TranslationServiceError(f"Ошибка перевода: {str(e)}")

    def _generate_cache_key(self, text, target_lang, source_lang, service):
        key_string = f"{text}_{target_lang}_{source_lang}_{service}"
        return f"translation:{hashlib.md5(key_string.encode()).hexdigest()}"

    def _get_from_database(self, text, target_language, source_language, service):
        try:
            return Translation.objects.filter(
                user=self.user,
                original_text=text,
                target_language=target_language,
                source_language=source_language,
                translator_service=service,
            ).first()
        except Exception:
            return None

    def _get_translator(self, service):
        translator_class = self.TRANSLATORS.get(service)
        if not translator_class:
            raise TranslationServiceError(f"Неподдерживаемый сервис: {service}")
        return translator_class()

    # ИЗМЕНЕНО: Метод теперь принимает 'alternatives' для сохранения в БД
    def _save_translation(
        self,
        text,
        translation_result,
        source_language,
        target_language,
        context=None,
        alternatives=None,
    ):
        return Translation.objects.create(
            user=self.user,
            original_text=text,
            translated_text=translation_result["translated_text"],
            source_language=source_language,
            target_language=target_language,
            translator_service=translation_result["service"],
            context=context,
            confidence=translation_result.get("confidence"),
            processing_time_ms=translation_result.get("processing_time_ms"),
            alternatives=alternatives or [],  # Сохраняем альтернативы
        )

    # ИЗМЕНЕНО: Метод теперь извлекает 'alternatives' из объекта модели
    def _format_db_result(self, translation_obj, start_time):
        return {
            "success": True,
            "original_text": translation_obj.original_text,
            "translated_text": translation_obj.translated_text,
            "alternatives": translation_obj.alternatives
            or [],  # Извлекаем альтернативы
            "source_language": translation_obj.source_language,
            "target_language": translation_obj.target_language,
            "service": translation_obj.translator_service,
            "cached": True,  # Означает, что данные взяты из постоянного хранилища (БД)
            "from_db": True,  # Добавим флаг для ясности
            "timestamp": timezone.now(),
            "processing_time_ms": round((time.time() - start_time) * 1000, 2),
        }

    # ИЗМЕНЕНО: Метод теперь включает 'alternatives' в финальный ответ
    def _format_translation_result(
        self,
        translation_result,
        text,
        source_language,
        target_language,
        start_time,
        alternatives=None,
    ):
        return {
            "success": True,
            "original_text": text,
            "translated_text": translation_result["translated_text"],
            "alternatives": alternatives or [],  # Включаем альтернативы
            "source_language": source_language,
            "target_language": target_language,
            "service": translation_result["service"],
            "confidence": translation_result.get("confidence"),
            "cached": False,
            "timestamp": timezone.now(),
            "processing_time_ms": round((time.time() - start_time) * 1000, 2),
        }

    def _genarate_suggestions_cache_key(self, word: str) -> str:
        key_string = f"suggestions_for_{word.lower().strip()}"
        return f"suggestions:{hashlib.md5(key_string.encode()).hexdigest()}"

    def _get_suggestions_from_cache(self, word: str) -> dict | None:
        cache_key = self._genarate_suggestions_cache_key(word)
        return cache.get(cache_key)

    def _set_suggestions_to_cache(self, word: str, suggestions: dict):
        cache_key = self._genarate_suggestions_cache_key(word)
        cache.set(cache_key, suggestions, timeout=60 * 60 * 24)

    # В файле services/translation_service.py

    def get_suggestions_for_flashcard(self, word: str, target_language: str) -> dict:
        """
        Комплексный метод для создания предложений для флеш-карточек.
        [ИСПРАВЛЕННАЯ ВЕРСИЯ СОГЛАСНО ДОКУМЕНТАЦИИ]
        """
        logger.info("--- [SERVICE] НАЧАЛО get_suggestions_for_flashcard ---")
        logger.info(f"[SERVICE] Ищем предложения для слова: '{word}', язык: '{target_language}'")

        # Проверка кэша, как и раньше
        cached_suggestions = self._get_suggestions_from_cache(word)
        if cached_suggestions:
            return cached_suggestions

        translator = self._get_translator("microsoft")

        try:
            # --- ШАГ 1: ВЫПОЛНЯЕМ ПОИСК ПО СЛОВАРЮ (lookup) ---
            # Это основной шаг, который дает нам и альтернативы, и основной перевод.
            # Мы НЕ используем больше общий translator.translate().
            # Важно: для этого шага нужен определенный исходный язык. 
            # Мы предполагаем, что он определяется на более раннем этапе или по умолчанию 'en'.
            # Для простоты пока захардкодим 'en', но в идеале его нужно определять.
            # !!! ВАЖНО: Если исходный язык может быть не 'en', его нужно определять ДО этого вызова.
            # Пока мы не знаем, как его получить, будем использовать временное решение.
            
            # Временное решение для определения языка. В идеале это должно быть в логике до.
            temp_translation_result = translator.translate(word, target_language, "auto")
            if not temp_translation_result or not temp_translation_result.get('success'):
                raise TranslationServiceError("Не удалось определить исходный язык для поиска по словарю.")
            
            detected_language = temp_translation_result.get('detected_language')
            if not detected_language or detected_language in ["und", "unknown"]:
                raise TranslationServiceError("Не удалось определить исходный язык для поиска по словарю.")

            logger.info(f"[SERVICE] Определен исходный язык: {detected_language}. Вызываем get_alternative_translations (lookup)...")
            
            alternatives = translator.get_alternative_translations(
                text=word,
                source_language=detected_language,
                target_language=target_language,
            )

            # --- ПРОВЕРКА РЕЗУЛЬТАТА ПОИСКА ПО СЛОВАРЮ ---
            if not alternatives:
                logger.warning(f"[SERVICE] Поиск по словарю для '{word}' не дал результатов. Возвращаем пустой ответ.")
                # Возвращаем только слово, чтобы пользователь мог ввести перевод вручную
                return {"word": word, "translation": "", "alternatives": [], "examples": []}

            # --- ШАГ 2: ИЗВЛЕКАЕМ ОСНОВНОЙ ПЕРЕВОД И ПРИМЕРЫ ---
            # Основной перевод - это первый и самый релевантный результат из поиска по словарю.
            main_translation_obj = alternatives[0]
            translated_text = main_translation_obj.get("text")

            if not translated_text:
                raise TranslationServiceError("Поиск по словарю вернул альтернативу без текста перевода.")

            logger.info(f"[SERVICE] Основной перевод из словаря: '{translated_text}'. Ищем для него примеры...")

            # Теперь вызываем get_examples с ПРАВИЛЬНЫМИ данными, полученными из ПОИСКА ПО СЛОВАРЮ
            examples = translator.get_examples(
                word=word,
                translation=translated_text,
                source_language=detected_language,
                target_language=target_language,
            )
            limited_examples = examples[:3]

            logger.info(f"[SERVICE] Получено примеров: {len(examples)}. Формируем итоговый ответ.")

            # --- Финальная сборка ответа ---
            suggestions = {
                "word": word,
                "translation": translated_text,
                # Собираем тексты всех альтернатив, включая основную
                "alternatives": [alt.get("text") for alt in alternatives if alt.get("text")],
                "examples": limited_examples,
            }
            self._set_suggestions_to_cache(word, suggestions)
            logger.info("[SERVICE] --- УСПЕШНОЕ ЗАВЕРШЕНИЕ get_suggestions_for_flashcard ---")
            return suggestions

        except TranslationServiceError as e:
            logger.error(f"[SERVICE] Перехвачена ошибка TranslationServiceError: {e}")
            raise
        except Exception as e:
            logger.error(f"[SERVICE] Перехвачена НЕОЖИДАННАЯ ошибка Exception: {e}", exc_info=True)
            raise TranslationServiceError(f"Внутренняя ошибка при генерации предложений: {str(e)}")