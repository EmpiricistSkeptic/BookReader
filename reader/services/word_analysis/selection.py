import re
import unicodedata

class InvalidSelectionError(ValueError):
    """Raised when the selected text cannot be analyzed."""
    pass
# Языки без пробелов между словами: счёт токенов по пробелам для них не работает.
NO_SPACE_LANGS = {"zh", "ja", "th", "lo", "km", "my"}

# Для таких языков выделение из <= N букв считаем одним словом без запроса классификации.
NO_SPACE_MAX_WORD_CHARS = 2

# Пробелы, en/em-тире и многоточие разделяют токены.
# Апострофы и дефисы внутри слова токен НЕ разделяют: l'homme, aujourd'hui, give-up.
_SPLIT_RE = re.compile(r"[\s–—―…]+")


def _is_word_char(ch: str) -> bool:
    # L - буквы, M - диакритика/огласовки (хинди, арабский), N - цифры.
    # Нельзя использовать \w: он не считает combining marks буквами
    # и разрезал бы слова на хинди и арабском.
    return unicodedata.category(ch)[0] in "LMN"


def count_tokens(text: str) -> int:
    return sum(
        1
        for chunk in _SPLIT_RE.split(text)
        if any(_is_word_char(ch) for ch in chunk)
    )


def detect_selection_form(text: str, source_language: str) -> str:
    """
    Возвращает:
      "word"        - одно слово
      "multi_token" - несколько слов (модель решит: expression или sentence)
      "unknown"     - язык без пробелов, форму определит модель
    """
    lang = (source_language or "").split("-")[0].lower()

    if lang in NO_SPACE_LANGS:
        chars = sum(1 for ch in text if _is_word_char(ch))
        return "word" if chars <= NO_SPACE_MAX_WORD_CHARS else "unknown"

    return "word" if count_tokens(text) <= 1 else "multi_token"