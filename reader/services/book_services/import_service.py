"""Оркестратор импорта книги: файл -> парсер -> Book + Chapter в БД."""
import logging
import time

from django.core.files.base import ContentFile
from django.db import transaction

from ...models import Book
from .archive import extract_upload_payload, safe_filename
from .exceptions import BookImportError, BookParseError
from .language import resolve_book_language
from .parsers.registry import default_registry

logger = logging.getLogger(__name__)


class BookImportService:
    """
    Импортирует загруженный файл (FB2, EPUB или ZIP с одной книгой).

    Парсинг выполняется до создания записей в БД.
    Книга и главы записываются в одной транзакции.
    Ожидаемые ошибки файла — BookImportError; остальные исключения
    пробрасываются как есть (после очистки файлового хранилища).
    """

    def __init__(self, registry=None):
        self.registry = registry or default_registry

    def import_book(self, user, uploaded_file, before_commit=None):
        original_filename = uploaded_file.name

        storage_files = []
        started_at = time.perf_counter()

        try:
            # ----------------------------------------------------
            # 1. Чтение и определение формата
            # ----------------------------------------------------

            read_started = time.perf_counter()
            uploaded_bytes = uploaded_file.read()
            read_seconds = time.perf_counter() - read_started

            extract_started = time.perf_counter()

            stored_filename, file_content, parser = extract_upload_payload(
                original_filename,
                uploaded_bytes,
                self.registry,
            )

            extract_seconds = time.perf_counter() - extract_started

            # ----------------------------------------------------
            # 2. Парсинг до начала транзакции записи
            # ----------------------------------------------------

            parse_started = time.perf_counter()

            parsed = parser.parse(file_content)

            if not parsed.chapters:
                raise BookParseError("В книге не удалось найти текст.")

            language = resolve_book_language(
                parsed.language,
                parsed.chapters,
            )

            parse_seconds = time.perf_counter() - parse_started

            # ----------------------------------------------------
            # 3. Создание книги и глав в одной транзакции
            # ----------------------------------------------------

            book_create_seconds = 0.0
            cover_save_seconds = 0.0
            file_save_seconds = 0.0
            book_save_seconds = 0.0
            chapters_save_seconds = 0.0
            serialization_seconds = 0.0

            transaction_started = time.perf_counter()

            with transaction.atomic():
                operation_started = time.perf_counter()

                book = Book.objects.create(
                    user=user,
                    title=parsed.title or "Без названия",
                    description=parsed.description or "",
                    book_format=parser.format_name,
                    authors=parsed.authors,
                    genres=parsed.genres,
                    language=language or "unknown",
                    file_size=len(file_content),
                )

                book_create_seconds = time.perf_counter() - operation_started

                if parsed.cover_content:
                    safe_cover_name = safe_filename(
                        parsed.cover_filename or "cover.jpg",
                        ".jpg",
                    )

                    operation_started = time.perf_counter()

                    book.cover.save(
                        safe_cover_name,
                        ContentFile(parsed.cover_content),
                        save=False,
                    )

                    cover_save_seconds = time.perf_counter() - operation_started

                    storage_files.append(
                        (book.cover.storage, book.cover.name)
                    )

                operation_started = time.perf_counter()

                book.file.save(
                    stored_filename,
                    ContentFile(file_content),
                    save=False,
                )

                file_save_seconds = time.perf_counter() - operation_started

                storage_files.append(
                    (book.file.storage, book.file.name)
                )

                operation_started = time.perf_counter()

                book.save()

                book_save_seconds = time.perf_counter() - operation_started

                operation_started = time.perf_counter()

                self._save_chapters(book, parsed.chapters)

                chapters_save_seconds = time.perf_counter() - operation_started

                if before_commit is not None:
                    operation_started = time.perf_counter()

                    before_commit(book)

                    serialization_seconds = time.perf_counter() - operation_started

            transaction_seconds = time.perf_counter() - transaction_started

            
            logger.info(
                (
                    "Book import completed: title=%r format=%s "
                    "bytes=%d chapters=%d "
                    "read=%.3fs extract=%.3fs parse=%.3fs "
                    "book_create=%.3fs cover_save=%.3fs "
                    "file_save=%.3fs book_save=%.3fs "
                    "chapters_save=%.3fs serialization=%.3fs "
                    "transaction=%.3fs total=%.3fs"
                ),
                book.title,
                parser.format_name,
                len(file_content),
                len(parsed.chapters),
                read_seconds,
                extract_seconds,
                parse_seconds,
                book_create_seconds,
                cover_save_seconds,
                file_save_seconds,
                book_save_seconds,
                chapters_save_seconds,
                serialization_seconds,
                transaction_seconds,
                time.perf_counter() - started_at,
            )


            return book

        except BookImportError as exc:
            self._cleanup_storage_files(storage_files)

            logger.warning(
                "Ошибка формата или обработки файла %r: %s",
                original_filename,
                exc,
            )

            raise

        except Exception:
            # Непредвиденные ошибки логирует вызывающий код.
            self._cleanup_storage_files(storage_files)
            raise

    @staticmethod
    def _save_chapters(book, chapters_data):
        """Сохраняет все главы одним массовым запросом."""
        ChapterModel = book.chapters.model

        objects = [
            ChapterModel(
                book=book,
                title=chapter["title"],
                content=chapter["content"],
                order=chapter["order"],
            )
            for chapter in chapters_data
        ]

        if objects:
            ChapterModel.objects.bulk_create(
                objects,
                batch_size=500,
            )

    @staticmethod
    def _cleanup_storage_files(storage_files):
        # Файловое хранилище не участвует в транзакции БД.
        # Поэтому удаляем уже сохранённые файлы, если запись
        # книги или глав завершилась ошибкой.
        for storage, name in reversed(storage_files):
            try:
                if name:
                    storage.delete(name)
            except Exception:
                logger.exception(
                    "Не удалось удалить файл после ошибки импорта: %s",
                    name,
                )
