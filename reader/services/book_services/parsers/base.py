from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ParsedBook:
    """
    Единый результат работы любого парсера.

    chapters — список {"title", "content", "order"}.
    language — «сырое» значение из метаданных (может быть пустым);
    итоговый язык определяет сервис импорта.
    """

    title: str = ""
    description: str = ""
    language: str = ""
    authors: str = ""
    genres: str = ""
    chapters: list = field(default_factory=list)
    cover_content: Optional[bytes] = None
    cover_filename: Optional[str] = None


class BaseBookParser:
    """
    Базовый класс парсера формата книги.

    Парсер не знает про Django, БД и HTTP: байты на входе,
    ParsedBook на выходе. Ошибки файла — BookImportError и наследники.
    """

    format_name = ""     # значение для Book.book_format, например "FB2"
    extensions = ()      # например (".fb2",)

    def parse(self, content: bytes) -> ParsedBook:
        raise NotImplementedError
