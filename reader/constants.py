from django.utils.translation import gettext_lazy as _


SUPPORTED_LANGUAGES = [
    ("ru", "ru"),
    ("en", "en"),
    ("de", "de"),
    ("fr", "fr"),
    ("es", "es"),
    ("it", "it"),
    ("pt", "pt"),
    ("pl", "pl"),
    ("nl", "nl"),
    ("ja", "ja"),
    ("zh", "zh"),
    ("ko", "ko"),
    ("ar", "ar"),
    ("hi", "hi"),
    ("tr", "tr"),
    ("uk", "uk"),
    ("bg", "bg"),
    ("cs", "cs"),
    ("id", "id"),
]

TRANSLATION_SERVICES = [
    ("deepl", "DeepL"),
    ("chatgpt", "ChatGPT"),
    ("google", "Google Translate"),
    ("microsoft", "Microsoft"),
]

LANGUAGE_NAMES = {
    "ru": _("Русский"),
    "en": _("Английский"),
    "de": _("Немецкий"),
    "fr": _("Французский"),
    "es": _("Испанский"),
    "it": _("Итальянский"),
    "pt": _("Португальский"),
    "pl": _("Польский"),
    "nl": _("Голландский"),
    "ja": _("Японский"),
    "zh": _("Китайский"),
    "ko": _("Корейский"),
    "ar": _("Арабский"),
    "hi": _("Хинди"),
    "tr": _("Турецкий"),
    "uk": _("Украинский"),
    "bg": _("Болгарский"),
    "cs": _("Чешский"),
    "id": _("Индонезийский"),
    "auto": _("Автоопределение"),
}

SERVICE_NAMES = {
    "deepl": "DeepL",
    "google": "Google Translate",
    "microsoft": "Microsoft",
    "chatgpt": "ChatGPT",
    "auto": _("Автоматический"),
}

CHATGPT_TRANSLATION_PROMPT_TEMPLATE = (
    "Ты — профессиональный переводчик. "
    "Переводи с {source_language} на {target_language}. "
    "Отвечай только переведённым текстом, без пояснений."
)


CHATGPT_EXAMPLES_PROMPT_TEMPLATE = (
    "Создай 3 коротких и понятных примера использования слова '{word}' в предложении. "
    "Слово означает '{translation}'. Ответ верни СТРОГО в формате JSON-массива (JSON array) из трёх строк. "
    'Например: ["Первый пример.", "Второй пример.", "Третий пример."]'
)
