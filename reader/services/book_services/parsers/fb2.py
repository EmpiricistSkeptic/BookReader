"""Парсер FB2."""
import base64
import binascii
import logging
import re
import xml.etree.ElementTree as ET

try:
    from defusedxml import ElementTree as SafeET
except ImportError:  # pragma: no cover
    SafeET = None

from ..archive import safe_filename
from ..chapters import make_chapter_unit, normalize_chapter_units
from ..exceptions import BookImportError, BookParseError
from .base import BaseBookParser, ParsedBook

logger = logging.getLogger(__name__)


class Fb2Parser(BaseBookParser):
    format_name = "FB2"
    extensions = (".fb2",)

    FB2_LINE_TAGS = frozenset({
        "p", "subtitle", "text-author", "v", "date",
    })

    FB2_IGNORED_BODIES = frozenset({
        "notes", "comments",
    })

    # ============================================================
    # ТОЧКА ВХОДА
    # ============================================================

    def parse(self, content):
        try:
            root = self._load_fb2_root(content)

            self._validate_fb2_structure(root)

            book_data = self._extract_fb2_metadata(root)

            chapters = self._extract_fb2_chapters(root)

            title_info = self._find_fb2_descendant(root, "title-info")

            cover_content, cover_filename = self._extract_fb2_cover(
                root,
                title_info=title_info,
            )

            return ParsedBook(
                title=book_data["title"],
                description=book_data["description"],
                language=book_data["language"],
                authors=book_data["authors"],
                genres=book_data["genres"],
                chapters=chapters,
                cover_content=cover_content,
                cover_filename=cover_filename,
            )

        except BookImportError:
            raise

        except Exception as exc:
            logger.exception("Ошибка парсинга FB2")

            raise BookParseError(
                f"Не удалось обработать FB2: {exc}"
            ) from exc

    # ============================================================
    # XML-ПОМОЩНИКИ
    # ============================================================

    @staticmethod
    def _xml_local_name(element):
        tag = getattr(element, "tag", "")

        if not isinstance(tag, str):
            return ""

        return tag.rsplit("}", 1)[-1].lower()

    @staticmethod
    def _xml_text(element):
        if element is None:
            return ""

        return " ".join(
            "".join(element.itertext()).split()
        )

    def _find_fb2_child(self, element, name):
        if element is None:
            return None

        for child in element:
            if self._xml_local_name(child) == name.lower():
                return child

        return None

    def _find_fb2_descendant(self, element, name):
        if element is None:
            return None

        for child in element.iter():
            if self._xml_local_name(child) == name.lower():
                return child

        return None

    def _load_fb2_root(self, file_content):
        """
        Сначала обычный безопасный XML-разбор.
        lxml recover — только резервный путь при ошибке разбора.
        """
        try:
            if SafeET is not None:
                return SafeET.fromstring(file_content)

            return ET.fromstring(file_content)

        except Exception as initial_error:
            logger.warning(
                "Обычный разбор FB2 не удался; пробую lxml recover: %s",
                initial_error,
            )

            try:
                from lxml import etree

                parser = etree.XMLParser(
                    recover=True,
                    resolve_entities=False,
                    no_network=True,
                    huge_tree=False,
                )

                root = etree.fromstring(file_content, parser=parser)

                if root is None:
                    raise ValueError("XML-парсер не вернул корневой элемент.")

                return root

            except Exception as fallback_error:
                raise BookParseError(
                    "Некорректный XML формата FB2."
                ) from fallback_error

    def _validate_fb2_structure(self, root):
        if self._find_fb2_descendant(root, "title-info") is None:
            raise BookParseError(
                "FB2-файл повреждён: отсутствует title-info."
            )

        if self._find_fb2_descendant(root, "body") is None:
            raise BookParseError(
                "FB2-файл повреждён: отсутствует body."
            )

    # ============================================================
    # МЕТАДАННЫЕ
    # ============================================================

    def _extract_fb2_metadata(self, root):
        title_info = self._find_fb2_descendant(root, "title-info")

        title_element = self._find_fb2_child(
            title_info,
            "book-title",
        )

        title = self._xml_text(title_element) or "Без названия"

        authors = []

        for author in title_info:
            if self._xml_local_name(author) != "author":
                continue

            first_name = self._xml_text(
                self._find_fb2_child(author, "first-name")
            )
            middle_name = self._xml_text(
                self._find_fb2_child(author, "middle-name")
            )
            last_name = self._xml_text(
                self._find_fb2_child(author, "last-name")
            )

            name = " ".join(
                part
                for part in (first_name, middle_name, last_name)
                if part
            )

            if name:
                authors.append(name)

        genres = []

        for element in title_info.iter():
            if self._xml_local_name(element) == "genre":
                genre = self._xml_text(element)

                if genre and genre not in genres:
                    genres.append(genre)

        language = self._xml_text(
            self._find_fb2_child(title_info, "lang")
        )

        annotation = self._find_fb2_descendant(
            title_info,
            "annotation",
        )

        description_paragraphs = []

        if annotation is not None:
            for element in annotation.iter():
                if self._xml_local_name(element) == "p":
                    text = self._xml_text(element)

                    if text:
                        description_paragraphs.append(text)

            if not description_paragraphs:
                text = self._xml_text(annotation)

                if text:
                    description_paragraphs.append(text)

        return {
            "title": title,
            "description": "\n\n".join(description_paragraphs),
            "language": language,
            "authors": ", ".join(authors),
            "genres": ", ".join(genres),
        }

    # ============================================================
    # ТЕКСТ И ГЛАВЫ
    # ============================================================

    def _collect_fb2_element(self, element, output):
        """
        Извлекает текстовые блоки в порядке XML-документа.

        Неизвестные контейнеры обходятся рекурсивно, поэтому содержимое,
        например, таблицы не теряется только из-за названия тега.
        """
        tag = self._xml_local_name(element)

        if tag in {"section", "binary", "image"}:
            return

        if tag in self.FB2_LINE_TAGS:
            text = self._xml_text(element)

            if text:
                output.append(text)

            return

        children = list(element)

        if not children:
            text = self._xml_text(element)

            if text:
                output.append(text)

            return

        # Текст перед первым дочерним элементом.
        direct_text = " ".join((element.text or "").split())

        if direct_text:
            output.append(direct_text)

        for child in children:
            self._collect_fb2_element(child, output)

            # В XML хвостовой текст находится после дочернего элемента.
            tail = " ".join((child.tail or "").split())

            if tail:
                output.append(tail)

    def _is_fb2_decorative_separator(self, text):
        """
        Определяет короткие декоративные разделители:
        * * *, ***, — — —, - - -, • • • и подобные.
        """
        compact = "".join((text or "").split())

        if compact == "⁂":
            return True

        separator_chars = {
            "*",
            "•",
            "·",
            "—",
            "–",
            "-",
            "_",
            "=",
            "~",
        }

        return (
            len(compact) >= 3
            and len(set(compact)) == 1
            and compact[0] in separator_chars
        )

    def _fb2_section_title(self, section):
        title_element = self._find_fb2_child(section, "title")

        if title_element is None:
            return ""

        pieces = []
        self._collect_fb2_element(title_element, pieces)

        return " ".join(" ".join(pieces).split())

    def _walk_fb2_section(self, section, units, parent_title=""):
        """
        Рекурсивно обходит FB2-секции.

        Сохраняет иерархию заголовков и текст родительских секций
        до, между и после дочерних секций.

        Декоративные разделители сохраняются в тексте, но сами по себе
        не создают защищённых глав.
        """
        raw_title = self._fb2_section_title(section)
        is_separator_title = self._is_fb2_decorative_separator(raw_title)

        # Формируем полный заголовок текущей секции.
        if raw_title and not is_separator_title:
            title = (
                f"{parent_title} — {raw_title}"
                if parent_title
                else raw_title
            )
        else:
            title = ""

        # Передаём дочерним секциям последний значимый заголовок.
        child_parent_title = title or parent_title

        buffer = []
        segment_number = 0
        saw_nested_section = False

        def flush_segment():
            nonlocal segment_number

            paragraphs = [
                text.strip()
                for text in buffer
                if text and text.strip()
            ]

            buffer.clear()

            if not paragraphs:
                return

            # Если фрагмент состоит только из декоративных разделителей,
            # он не должен становиться защищённой главой.
            has_real_text = any(
                not self._is_fb2_decorative_separator(text)
                for text in paragraphs
            )

            if title and has_real_text:
                if segment_number == 0 and not saw_nested_section:
                    segment_title = title
                else:
                    continuation_number = max(1, segment_number)

                    suffix = (
                        " (продолжение)"
                        if continuation_number == 1
                        else f" (продолжение {continuation_number})"
                    )

                    segment_title = f"{title}{suffix}"

                protected = True
                origin = "section"
            else:
                segment_title = ""
                protected = False
                origin = "technical"

            units.append(
                make_chapter_unit(
                    segment_title,
                    paragraphs,
                    protected=protected,
                    origin=origin,
                )
            )

            segment_number += 1

        for child in section:
            tag = self._xml_local_name(child)

            if tag == "title":
                # Обычный заголовок уже используется как название секции.
                # Но декоративный заголовок сохраняем как текстовый фрагмент.
                if is_separator_title and raw_title:
                    buffer.append(raw_title)

            elif tag == "section":
                flush_segment()

                self._walk_fb2_section(
                    child,
                    units,
                    parent_title=child_parent_title,
                )

                saw_nested_section = True

            else:
                self._collect_fb2_element(child, buffer)

            # Обрабатываем tail и для title, чтобы не пропускать текст
            # между соседними XML-элементами.
            tail = " ".join((child.tail or "").split())

            if tail:
                buffer.append(tail)

        flush_segment()

    def _extract_fb2_chapters(self, root):
        units = []

        for body in root.iter():
            if self._xml_local_name(body) != "body":
                continue

            if (body.get("name") or "").lower() in self.FB2_IGNORED_BODIES:
                continue

            children = list(body)

            has_sections = any(
                self._xml_local_name(child) == "section"
                for child in children
            )

            raw_body_title = (
                self._fb2_section_title(body)
                if has_sections
                else ""
            )

            body_title_is_separator = self._is_fb2_decorative_separator(
                raw_body_title
            )

            body_title = (
                ""
                if body_title_is_separator
                else raw_body_title
            )

            buffer = []

            def flush_body_text():
                paragraphs = [
                    text.strip()
                    for text in buffer
                    if text and text.strip()
                ]

                buffer.clear()

                if paragraphs:
                    units.append(
                        make_chapter_unit(
                            "",
                            paragraphs,
                            protected=False,
                            origin="technical",
                        )
                    )

            for child in children:
                tag = self._xml_local_name(child)

                if tag == "title" and has_sections:
                    # Заголовок body передаётся дочерним секциям.
                    # Декоративный заголовок при этом сохраняем как текст.
                    if body_title_is_separator and raw_body_title:
                        buffer.append(raw_body_title)

                elif tag == "section":
                    flush_body_text()

                    self._walk_fb2_section(
                        child,
                        units,
                        parent_title=body_title,
                    )

                else:
                    self._collect_fb2_element(child, buffer)

                tail = " ".join((child.tail or "").split())

                if tail:
                    buffer.append(tail)

            flush_body_text()

        return normalize_chapter_units(units)

    # ============================================================
    # ОБЛОЖКА
    # ============================================================

    def _extract_fb2_cover(self, root, title_info=None):
        if title_info is None:
            title_info = self._find_fb2_descendant(root, "title-info")

        if title_info is None:
            return None, None

        coverpage = self._find_fb2_child(title_info, "coverpage")

        if coverpage is None:
            return None, None

        image_element = self._find_fb2_descendant(coverpage, "image")

        if image_element is None:
            return None, None

        href = None

        for attr_name, attr_value in image_element.attrib.items():
            if attr_name.rsplit("}", 1)[-1].lower() == "href":
                href = attr_value
                break

        if not href:
            return None, None

        cover_id = href.lstrip("#").strip()

        for element in root.iter():
            if self._xml_local_name(element) != "binary":
                continue

            if element.get("id") != cover_id or not element.text:
                continue

            try:
                encoded = re.sub(r"\s+", "", element.text)
                image_bytes = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                logger.warning(
                    "Не удалось декодировать обложку FB2: %s",
                    cover_id,
                )
                return None, None

            content_type = (
                element.get("content-type") or "image/jpeg"
            ).lower()

            extensions = {
                "image/jpeg": ".jpg",
                "image/jpg": ".jpg",
                "image/png": ".png",
                "image/gif": ".gif",
                "image/webp": ".webp",
            }

            extension = extensions.get(content_type, ".jpg")
            filename = safe_filename(cover_id, extension)

            return image_bytes, filename

        return None, None
