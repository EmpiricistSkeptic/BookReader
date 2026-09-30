import unicodedata

MAX_WORD_LEN = 64
_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "`": "'", "´": "'"})
_JOINERS = "'-"  # допустимы только внутри слова: don't, well-known


def _is_word_char(ch: str) -> bool:
    # L* — буквы, M* — комбинирующие знаки (важно для хинди, арабского и т.п.)
    return unicodedata.category(ch)[0] in ("L", "M")


def _is_strippable(ch: str) -> bool:
    # P* пунктуация, S* символы, Z* пробелы, C* управляющие. Цифры НЕ срезаем.
    return unicodedata.category(ch)[0] in ("P", "S", "Z", "C")


def normalize_word(raw: str | None) -> str | None:
    """Возвращает нормализованное слово или None, если это не ровно одно слово."""
    if not raw:
        return None

    text = unicodedata.normalize("NFKC", raw).translate(_APOSTROPHES)

    start, end = 0, len(text)
    while start < end and _is_strippable(text[start]):
        start += 1
    while end > start and _is_strippable(text[end - 1]):
        end -= 1

    core = text[start:end].lower()
    if not core or len(core) > MAX_WORD_LEN:
        return None

    prev_joiner = False
    for ch in core:
        if _is_word_char(ch):
            prev_joiner = False
        elif ch in _JOINERS and not prev_joiner:
            prev_joiner = True
        else:  # пробел, цифра, "a--b", "make—take" и т.д.
            return None
    return core