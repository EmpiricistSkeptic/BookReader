"""ZIP: лимиты, извлечение единственной книги, безопасные имена файлов."""
import io
import zipfile

from .exceptions import InvalidArchiveError, UnsupportedFormatError

MAX_ARCHIVE_SINGLE_FILE = 100 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 150 * 1024 * 1024
MAX_ARCHIVE_ENTRIES = 5_000
MAX_COMPRESSION_RATIO = 1_000


def safe_filename(filename, expected_extension=None):
    """Оставляет только имя файла и при необходимости исправляет расширение."""
    filename = str(filename or "book").replace("\\", "/")
    filename = filename.split("/")[-1].strip()

    if not filename or filename in {".", ".."}:
        filename = "book"

    if expected_extension:
        stem = filename.rsplit(".", 1)[0] if "." in filename else filename
        filename = f"{stem}{expected_extension}"

    return filename


def check_zip_limits(archive, archive_label="архив"):
    """Проверяет распакованный размер, количество файлов и степень сжатия."""
    infos = [
        info
        for info in archive.infolist()
        if not info.is_dir()
    ]

    if len(infos) > MAX_ARCHIVE_ENTRIES:
        raise InvalidArchiveError(
            f"{archive_label}: слишком много файлов в архиве."
        )

    total_unpacked = 0

    for info in infos:
        if info.file_size > MAX_ARCHIVE_SINGLE_FILE:
            raise InvalidArchiveError(
                f"{archive_label}: один из файлов слишком большой."
            )

        total_unpacked += info.file_size

        if total_unpacked > MAX_ARCHIVE_TOTAL_BYTES:
            raise InvalidArchiveError(
                f"{archive_label}: превышен лимит распакованного размера."
            )

        if info.file_size and not info.compress_size:
            raise InvalidArchiveError(
                f"{archive_label}: некорректный размер сжатого файла."
            )

        if info.compress_size:
            ratio = info.file_size / info.compress_size

            if ratio > MAX_COMPRESSION_RATIO:
                raise InvalidArchiveError(
                    f"{archive_label}: подозрительная степень сжатия."
                )

    return infos


def extract_upload_payload(original_filename, uploaded_bytes, registry):
    """
    Возвращает (имя сохраняемого файла, байты книги, парсер).

    Формат определяется по расширению через реестр парсеров.
    ZIP должен содержать ровно один файл поддерживаемого формата.
    Извлечённая книга не сохраняется под расширением .zip.
    """
    lowered = original_filename.lower()

    match = registry.find(original_filename)

    if match is not None:
        extension, parser = match
        return (
            safe_filename(original_filename, extension),
            uploaded_bytes,
            parser,
        )

    if not lowered.endswith(".zip"):
        raise UnsupportedFormatError(
            f"Поддерживаются только {', '.join(registry.format_names)} и ZIP."
        )

    try:
        with zipfile.ZipFile(io.BytesIO(uploaded_bytes)) as archive:
            infos = check_zip_limits(archive, "ZIP")

            candidates = [
                info
                for info in infos
                if not info.filename.replace("\\", "/").startswith("__MACOSX/")
                and registry.find(info.filename) is not None
            ]

            if not candidates:
                raise InvalidArchiveError(
                    f"В ZIP не найден файл {' или '.join(registry.format_names)}."
                )

            if len(candidates) > 1:
                raise InvalidArchiveError(
                    "В ZIP найдено несколько книг. "
                    "Оставь в архиве только одну книгу."
                )

            selected = candidates[0]

            if selected.file_size > MAX_ARCHIVE_SINGLE_FILE:
                raise InvalidArchiveError(
                    "Файл книги внутри ZIP слишком большой."
                )

            with archive.open(selected) as source:
                file_content = source.read(MAX_ARCHIVE_SINGLE_FILE + 1)

            if len(file_content) > MAX_ARCHIVE_SINGLE_FILE:
                raise InvalidArchiveError(
                    "Файл книги внутри ZIP слишком большой."
                )

            extension, parser = registry.find(selected.filename)

            stored_filename = safe_filename(selected.filename, extension)

            return stored_filename, file_content, parser

    except zipfile.BadZipFile as exc:
        raise InvalidArchiveError("Повреждённый ZIP-архив.") from exc
