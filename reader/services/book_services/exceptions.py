class BookImportError(Exception):
    """
    Ожидаемая ошибка импорта книги (проблема в файле, а не в коде).

    Вьюха превращает её в HTTP 400. Всё остальное считается
    непредвиденной ошибкой (HTTP 500).
    """


class UnsupportedFormatError(BookImportError):
    """Формат файла не поддерживается."""


class InvalidArchiveError(BookImportError):
    """ZIP повреждён, нарушает лимиты или содержит не одну книгу."""


class BookParseError(BookImportError):
    """Не удалось разобрать содержимое книги."""
