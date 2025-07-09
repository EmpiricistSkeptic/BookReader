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
]

TRANSLATION_SERVICES = [
    ("auto", _("Автоматический выбор")),
    ("deepl", "DeepL"),
    ("google", "Google Translate"),
    ("contextil", "Contextil"),
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
    "auto": _("Автоопределение"),
}

SERVICE_NAMES = {
    "deepl": "DeepL",
    "google": "Google Translate",
    "contextil": "Contextil",
    "auto": _("Автоматический"),
}

CHATGPT_TRANSLATION_PROMPT_TEMPLATE = (
    "Ты — профессиональный переводчик. "
    "Переводи с {source_language} на {target_language}. "
    "Отвечай только переведённым текстом, без пояснений."
)
