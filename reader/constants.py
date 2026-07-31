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
    ("deepseek", "DeepSeek"),
]

LANGUAGE_NAMES = {
    "ru": _("Russian"),
    "en": _("English"),
    "de": _("German"),
    "fr": _("French"),
    "es": _("Spanish"),
    "it": _("Italian"),
    "pt": _("Portuguese"),
    "pl": _("Polish"),
    "nl": _("Dutch"),
    "ja": _("Japanese"),
    "zh": _("Chinese"),
    "ko": _("Korean"),
    "ar": _("Arabic"),
    "hi": _("Hindi"),
    "tr": _("Turkish"),
    "uk": _("Ukrainian"),
    "bg": _("Bulgarian"),
    "cs": _("Czech"),
    "id": _("Indonesian"),
    "auto": _("Auto Detect"),
}

SERVICE_NAMES = {
    "deepl": "DeepL",
    "google": "Google Translate",
    "microsoft": "Microsoft",
    "chatgpt": "ChatGPT",
    "deepseek": "DeepSeek",
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

DEEPSEEK_TRANSLATION_PROMPT_TEMPLATE = (
    "You are a professional translator. "
    "Translate the given text from {source_language} to {target_language}. "
    "Maintain the original tone, context, and formatting. "
    "Respond ONLY with the translated text, without any explanations, notes, or quotation marks."
)

DEEPSEEK_EXAMPLES_PROMPT_TEMPLATE = (
    "Create 3 short, clear, and natural example sentences using the word '{word}' (which means '{translation}'). "
    "Return the response STRICTLY as a valid JSON array of 3 strings. "
    "Do NOT include Markdown code blocks, introductory text, or commentary. "
    'Example output format: ["First example sentence.", "Second example sentence.", "Third example sentence."]'
)

DEEPSEEK_ALTERNATIVES_PROMPT_TEMPLATE = """
You are a professional translator.

Your task is to suggest alternative translations for a word or short phrase.

Rules:
- Return ONLY valid JSON.
- Do not use markdown.
- Do not wrap the JSON in ```json blocks.
- Do not add explanations before or after the JSON.
- Return between 2 and 5 alternative translations.
- Exclude the primary translation if possible.
- Alternatives must fit the original word in its context.
- Do not invent meanings that are not valid.

JSON format:

[
    {
        "text": "alternative translation"
    }
]
"""