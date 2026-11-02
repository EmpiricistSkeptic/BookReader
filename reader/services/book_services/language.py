"""Нормализация и определение языка книги."""
import logging

logger = logging.getLogger(__name__)

_LANGUAGE_ALIASES = {
    "eng": "en",
    "spa": "es",
    "fra": "fr",
    "fre": "fr",
    "deu": "de",
    "ger": "de",
    "ukr": "uk",
    "rus": "ru",
    "por": "pt",
    "ita": "it",
    "zho": "zh",
    "chi": "zh",
    "jpn": "ja",
}


def normalize_language_code(language):
    if not language:
        return ""

    code = (
        str(language)
        .strip()
        .lower()
        .replace("_", "-")
        .split("-")[0]
    )

    return _LANGUAGE_ALIASES.get(code, code)


def supported_language_codes():
    """Коды языков из choices поля Book.language."""
    try:
        # Импорт внутри функции: модуль остаётся импортируемым
        # без инициализированного Django-приложения.
        from ..models import Book

        choices = Book._meta.get_field("language").choices
        return {
            normalize_language_code(code)
            for code, _label in choices
            if code
        }
    except Exception:
        return set()


def detect_language_from_content(chapters_data):
    text_sample = "\n".join(
        chapter.get("content", "")
        for chapter in chapters_data[:5]
    )[:5_000]

    if not text_sample.strip():
        return None

    try:
        from langdetect import DetectorFactory, detect

        DetectorFactory.seed = 0
        detected = normalize_language_code(
            detect(text_sample)
        )

        supported = supported_language_codes()

        if detected and (
            not supported or detected in supported
        ):
            return detected

    except Exception:
        logger.warning(
            "Не удалось определить язык книги по тексту.",
            exc_info=True,
        )

    return None


def resolve_book_language(metadata_language, chapters_data):
    """
    Сначала валидные метаданные, затем определение по тексту.
    Не присваивает поддерживаемый языковой код только потому,
    что он является значением по умолчанию модели.
    """
    supported = supported_language_codes()
    metadata_code = normalize_language_code(metadata_language)

    if metadata_code and (
        not supported or metadata_code in supported
    ):
        return metadata_code

    detected = detect_language_from_content(chapters_data)

    return detected or "unknown"
