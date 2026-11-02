"""Реестр парсеров: расширение файла -> класс парсера."""
from .epub import EpubParser
from .fb2 import Fb2Parser


class ParserRegistry:
    def __init__(self):
        self._parsers = []

    def register(self, parser):
        """Регистрирует экземпляр парсера. Возвращает его же."""
        self._parsers.append(parser)
        return parser

    @property
    def format_names(self):
        return [parser.format_name for parser in self._parsers]

    def find(self, filename):
        """
        Ищет парсер по расширению имени файла.

        Возвращает (расширение, парсер) или None.
        """
        lowered = (filename or "").lower()

        for parser in self._parsers:
            for extension in parser.extensions:
                if lowered.endswith(extension):
                    return extension, parser

        return None


default_registry = ParserRegistry()
default_registry.register(Fb2Parser())
default_registry.register(EpubParser())
