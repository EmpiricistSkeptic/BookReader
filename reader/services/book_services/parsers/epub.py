"""Парсер EPUB."""
import io
import logging
import posixpath
import re
import tempfile
import zipfile
from html.entities import html5 as HTML5_ENTITIES
from urllib.parse import unquote

import ebooklib
from bs4 import (
    BeautifulSoup,
    Comment,
    Declaration,
    Doctype,
    NavigableString,
    ProcessingInstruction,
    Tag,
    UnicodeDammit,
)
from ebooklib import epub

from ..archive import check_zip_limits
from ..chapters import (
    MAX_CHAPTER_CHARS,
    MIN_CHAPTER_CHARS,
    chapter_char_count,
    make_chapter_unit,
    normalize_chapter_units,
)
from ..exceptions import BookImportError, BookParseError, InvalidArchiveError
from .base import BaseBookParser, ParsedBook

logger = logging.getLogger(__name__)


class EpubParser(BaseBookParser):
    format_name = "EPUB"
    extensions = (".epub",)

    EPUB_BLOCK_TAGS = frozenset({
        "p", "div", "section", "article", "aside",
        "header", "footer", "main",
        "h1", "h2", "h3", "h4", "h5", "h6",
        "li", "ul", "ol", "dl", "dt", "dd",
        "blockquote", "pre", "table", "thead", "tbody",
        "tfoot", "tr", "td", "th", "caption",
        "figure", "figcaption", "hr", "address", "center",
    })

    EPUB_SKIP_TAGS = frozenset({
        "script", "style", "head", "title",
        "meta", "link", "svg", "noscript",
    })

    # ============================================================
    # ТОЧКА ВХОДА
    # ============================================================

    def parse(self, content):
        """
        EPUB: проверка архива, чтение spine, разбор текста,
        восстановление границ TOC/якорей и нормализация размеров.
        """
        self._validate_epub_archive(content)

        try:
            with tempfile.NamedTemporaryFile(
                suffix=".epub",
                delete=True,
            ) as tmp:
                tmp.write(content)
                tmp.flush()

                book_epub = epub.read_epub(tmp.name)

                title_metadata = book_epub.get_metadata("DC", "title")
                title = (
                    title_metadata[0][0]
                    if title_metadata and title_metadata[0][0]
                    else "Без названия"
                )

                author_metadata = book_epub.get_metadata("DC", "creator")
                authors = ", ".join(
                    item[0]
                    for item in (author_metadata or [])
                    if item and item[0]
                )

                subject_metadata = book_epub.get_metadata("DC", "subject")
                genres = ", ".join(
                    item[0]
                    for item in (subject_metadata or [])
                    if item and item[0]
                )

                description_metadata = book_epub.get_metadata(
                    "DC",
                    "description",
                )

                description = ""

                if description_metadata and description_metadata[0][0]:
                    description = BeautifulSoup(
                        description_metadata[0][0],
                        "lxml",
                    ).get_text(" ", strip=True)

                language_metadata = book_epub.get_metadata(
                    "DC",
                    "language",
                )

                metadata_language = (
                    language_metadata[0][0]
                    if language_metadata
                    else None
                )

                cover_content, cover_filename = (
                    self._extract_epub_cover(book_epub)
                )

                # -----------------------------------------------
                # 1. Документы в порядке чтения из spine
                # -----------------------------------------------

                items = []
                seen_names = set()

                for entry in book_epub.spine or []:
                    if isinstance(entry, (tuple, list)):
                        idref = entry[0]
                        linear = entry[1] if len(entry) > 1 else "yes"
                    else:
                        idref = entry
                        linear = "yes"

                    if str(linear).lower() == "no":
                        continue

                    item = book_epub.get_item_with_id(idref)

                    if (
                        item is not None
                        and self._is_epub_document(item)
                        and item.get_name() not in seen_names
                    ):
                        items.append(item)
                        seen_names.add(item.get_name())

                # Резервный обход manifest используем только тогда,
                # когда EPUB вообще не содержит spine.
                # Если spine существует, соблюдаем его порядок и linear="no".
                if not (book_epub.spine or []):
                    for item in book_epub.get_items():
                        name = item.get_name()

                        if (
                            self._is_epub_document(item)
                            and name not in seen_names
                        ):
                            items.append(item)
                            seen_names.add(name)

                # -----------------------------------------------
                # 2. Одна лента абзацев и глобальные позиции
                # -----------------------------------------------

                all_paragraphs = []
                doc_info = {}
                document_order = []
                global_headings = {}

                for item in items:
                    name = item.get_name()
                    offset = len(all_paragraphs)

                    extracted = self._extract_epub_document(
                        item.get_content()
                    )

                    paragraphs = extracted["paragraphs"]

                    # Пустые документы не формируют пустые главы.
                    if not paragraphs:
                        continue

                    doc_info[name] = {
                        "offset": offset,
                        "extracted": extracted,
                    }

                    document_order.append(name)
                    all_paragraphs.extend(paragraphs)

                    for local_position, heading in (
                        extracted["headings"].items()
                    ):
                        global_headings[offset + local_position] = heading

                if not all_paragraphs:
                    raise BookParseError("В EPUB не удалось найти текст.")

                # -----------------------------------------------
                # 3. TOC и якоря
                # -----------------------------------------------

                toc_entries = []
                self._flatten_epub_toc(
                    book_epub.toc or [],
                    toc_entries,
                )

                units = self._build_epub_units(
                    all_paragraphs,
                    document_order,
                    doc_info,
                    global_headings,
                    toc_entries,
                )

                chapters_data = normalize_chapter_units(units)

                logger.info(
                    "EPUB parsed: title=%r documents=%d chapters=%d",
                    title,
                    len(document_order),
                    len(chapters_data),
                )

                return ParsedBook(
                    title=title,
                    description=description,
                    language=metadata_language or "",
                    authors=authors,
                    genres=genres,
                    chapters=chapters_data,
                    cover_content=cover_content,
                    cover_filename=cover_filename,
                )

        except BookImportError:
            raise

        except Exception as exc:
            logger.exception("Ошибка парсинга EPUB")

            raise BookParseError(
                f"Не удалось обработать EPUB: {exc}"
            ) from exc

    # ============================================================
    # ВАЛИДАЦИЯ И ОТБОР ДОКУМЕНТОВ
    # ============================================================

    def _validate_epub_archive(self, file_content):
        try:
            with zipfile.ZipFile(io.BytesIO(file_content)) as archive:
                check_zip_limits(archive, "EPUB")

        except zipfile.BadZipFile as exc:
            raise InvalidArchiveError(
                "EPUB содержит повреждённый ZIP-архив."
            ) from exc

    def _is_epub_document(self, item):
        """
        Проверяет, является ли элемент EPUB обычным текстовым документом.

        Исключает:
        - EpubNav — навигационный XHTML-документ;
        - элементы с ITEM_NAVIGATION;
        - элементы с properties="nav".

        Оставляет обычные XHTML/HTML-документы и ITEM_DOCUMENT.
        """

        # ---------------------------------------------------------
        # 1. Проверяем класс EpubNav
        # ---------------------------------------------------------

        nav_class = getattr(epub, "EpubNav", None)

        if nav_class is not None and isinstance(item, nav_class):
            return False

        # ---------------------------------------------------------
        # 2. Проверяем тип элемента EbookLib
        # ---------------------------------------------------------

        try:
            item_type = item.get_type()
        except Exception:
            item_type = None

        navigation_type = getattr(
            ebooklib,
            "ITEM_NAVIGATION",
            None,
        )

        if (
            navigation_type is not None
            and item_type == navigation_type
        ):
            return False

        # ---------------------------------------------------------
        # 3. Проверяем properties из manifest EPUB
        # ---------------------------------------------------------

        properties = getattr(item, "properties", None) or []

        if isinstance(properties, str):
            properties = properties.split()

        try:
            normalized_properties = {
                str(value).lower()
                for value in properties
            }
        except TypeError:
            normalized_properties = set()

        if "nav" in normalized_properties:
            return False

        # ---------------------------------------------------------
        # 4. Проверяем MIME-тип
        # ---------------------------------------------------------

        media_type = (
            getattr(item, "media_type", "") or ""
        ).split(";", 1)[0].strip().lower()

        html_types = {
            "application/xhtml+xml",
            "text/html",
            "application/x-dtbook+xml",
        }

        if media_type in html_types:
            return True

        # ---------------------------------------------------------
        # 5. Последняя проверка — тип документа
        # ---------------------------------------------------------

        return item_type == ebooklib.ITEM_DOCUMENT

    # ============================================================
    # ИЗВЛЕЧЕНИЕ ТЕКСТА ИЗ XHTML
    # ============================================================

    def _extract_epub_document(self, content):
        """
        Извлекает абзацы, позиции якорей и позиции заголовков из XHTML.

        Перед XML-разбором:
        - определяет кодировку;
        - нормализует HTML5 named entities;
        - сохраняет пять стандартных XML-сущностей.

        При извлечении:
        - одиночный <br> сохраняется как перенос внутри абзаца;
        - два и более последовательных <br> становятся границей абзацев;
        - вложенные block-элементы не дублируют текст.
        """

        # ---------------------------------------------------------
        # 1. Декодирование исходного содержимого
        # ---------------------------------------------------------

        if isinstance(content, bytes):
            markup = content
        elif isinstance(content, (bytearray, memoryview)):
            markup = bytes(content)
        elif isinstance(content, str):
            markup = content
        else:
            raise BookParseError(
                "Неподдерживаемый тип содержимого XHTML."
            )

        decoder = UnicodeDammit(
            markup,
            is_html=False,
        )

        decoded_content = decoder.unicode_markup

        if decoded_content is None:
            raise BookParseError(
                "Не удалось определить кодировку XHTML."
            )

        # ---------------------------------------------------------
        # 2. Нормализация именованных HTML5-сущностей
        # ---------------------------------------------------------

        xml_predefined_entities = {
            "amp",
            "lt",
            "gt",
            "quot",
            "apos",
        }

        entity_pattern = re.compile(
            r"&([A-Za-z][A-Za-z0-9]*);"
        )

        def replace_html_entity(match):
            entity_name = match.group(1)

            if entity_name in xml_predefined_entities:
                return match.group(0)

            replacement = HTML5_ENTITIES.get(
                f"{entity_name};"
            )

            if replacement is None:
                return match.group(0)

            return replacement

        normalized_content = entity_pattern.sub(
            replace_html_entity,
            decoded_content,
        )

        # ---------------------------------------------------------
        # 3. Разбор XHTML
        # ---------------------------------------------------------

        soup = None
        body = None

        for parser in ("xml", "lxml"):
            try:
                candidate = BeautifulSoup(
                    normalized_content,
                    parser,
                )

                candidate_body = candidate.find("body")

                if candidate_body is not None:
                    soup = candidate
                    body = candidate_body
                    break

                if soup is None:
                    soup = candidate

            except Exception:
                logger.warning(
                    "Не удалось разобрать XHTML с помощью %s",
                    parser,
                    exc_info=True,
                )

        if body is None:
            if soup is None:
                raise BookParseError(
                    "Не удалось прочитать HTML-содержимое EPUB."
                )

            body = soup

        # ---------------------------------------------------------
        # 4. Подготовка структуры документа
        # ---------------------------------------------------------

        paragraphs = []
        anchors = {}
        headings = {}
        buffer = []

        # Специальный внутренний маркер настоящего HTML-тега <br>.
        # Он позволяет не путать <br> с обычными переносами
        # строк в исходном XHTML.
        BR_MARKER = ""

        def flush():
            if not buffer:
                return

            raw = "".join(buffer)
            buffer.clear()

            # Разделяем содержимое только по двум или более
            # последовательным тегам <br>.
            #
            # Пробелы и переносы между тегами допускаются:
            # <br><br>
            # <br />
            # <br />
            #
            # Одиночный <br> останется переносом внутри абзаца.

            br_separator = re.compile(
                rf"{re.escape(BR_MARKER)}"
                rf"(?:\s*{re.escape(BR_MARKER)})+"
            )

            parts = br_separator.split(raw)

            for part in parts:
                if not part.strip():
                    continue

                # Одиночный <br> превращаем в перенос внутри абзаца.
                part = part.replace(BR_MARKER, "\n")

                lines = part.splitlines() or [part]

                text = "\n".join(
                    " ".join(line.split())
                    for line in lines
                ).strip()

                if text:
                    paragraphs.append(text)

        def register_block_anchors(node):
            for key in ("id", "name"):
                value = node.get(key)

                if isinstance(value, str) and value.strip():
                    anchors.setdefault(
                        value.strip(),
                        len(paragraphs),
                    )

        # ---------------------------------------------------------
        # 5. Рекурсивный обход HTML-дерева
        # ---------------------------------------------------------

        def walk(node):
            if isinstance(node, NavigableString):
                if not isinstance(
                    node,
                    (
                        Comment,
                        Declaration,
                        Doctype,
                        ProcessingInstruction,
                    ),
                ):
                    buffer.append(str(node))

                return

            if not isinstance(node, Tag):
                return

            tag_name = (node.name or "").lower()

            if tag_name in self.EPUB_SKIP_TAGS:
                return

            if tag_name == "br":
                # Не добавляем обычный перенос напрямую.
                # Сохраняем отдельный маркер, чтобы flush()
                # мог отличить один <br> от двух последовательных.
                buffer.append(BR_MARKER)
                return

            is_block = tag_name in self.EPUB_BLOCK_TAGS

            if is_block:
                flush()
                register_block_anchors(node)

                heading_level = None

                if (
                    len(tag_name) == 2
                    and tag_name[0] == "h"
                    and tag_name[1].isdigit()
                ):
                    heading_level = int(tag_name[1])

                start_index = len(paragraphs)

                for child in node.children:
                    walk(child)

                flush()

                if (
                    heading_level is not None
                    and start_index < len(paragraphs)
                ):
                    heading_text = (
                        paragraphs[start_index]
                        .replace("\n", " ")
                        .strip()
                    )

                    if heading_text:
                        headings[start_index] = {
                            "title": heading_text,
                            "level": heading_level,
                        }

                return

            # Встроенные элементы: i, b, span, a и другие.
            # Не создаём искусственных пробелов вокруг их текста.
            for key in ("id", "name"):
                value = node.get(key)

                if not isinstance(value, str) or not value.strip():
                    continue

                pending_text = "".join(buffer)

                position = len(paragraphs)

                if pending_text.strip():
                    position += 1

                anchors.setdefault(value.strip(), position)

            for child in node.children:
                walk(child)

        for child in body.children:
            walk(child)

        flush()

        return {
            "paragraphs": paragraphs,
            "anchors": anchors,
            "headings": headings,
        }

    # ============================================================
    # TOC
    # ============================================================

    def _flatten_epub_toc(self, toc, output):
        """Преобразует вложенный TOC EbookLib в последовательный список."""
        for entry in toc or []:
            if isinstance(entry, (list, tuple)):
                if not entry:
                    continue

                head = entry[0]
                children = entry[1] if len(entry) > 1 else []

                self._append_epub_toc_node(head, output)
                self._flatten_epub_toc(children, output)

            else:
                self._append_epub_toc_node(entry, output)

    def _append_epub_toc_node(self, node, output):
        href = getattr(node, "href", None)

        if isinstance(node, str):
            href = node

        if not href:
            return

        title = getattr(node, "title", "") or ""

        if isinstance(title, (bytes, bytearray)):
            title = title.decode("utf-8", errors="replace")

        title = BeautifulSoup(
            str(title),
            "html.parser",
        ).get_text(" ", strip=True)

        output.append((str(href), title))

    def _resolve_epub_document(self, href, doc_info, by_basename):
        path = unquote(
            href.split("#", 1)[0].split("?", 1)[0]
        ).replace("\\", "/")

        if not path:
            return None

        normalized = posixpath.normpath(path).lstrip("/")

        while normalized.startswith("./"):
            normalized = normalized[2:]

        if normalized in doc_info:
            return normalized

        # Сопоставляем нормализованные пути.
        for name in doc_info:
            candidate = posixpath.normpath(name).lstrip("/")

            while candidate.startswith("./"):
                candidate = candidate[2:]

            if candidate == normalized:
                return name

        # Последний вариант — уникальное совпадение имени файла.
        matches = by_basename.get(
            posixpath.basename(normalized).lower(),
            [],
        )

        return matches[0] if len(matches) == 1 else None

    # ============================================================
    # ГЛАВЫ: РАЗДЕЛЕНИЕ ПО ЗАГОЛОВКАМ И СБОРКА ИЗ TOC
    # ============================================================

    def _split_large_epub_unit_by_headings(
        self,
        unit,
        start,
        end,
        all_paragraphs,
        global_headings,
        force=False,
    ):
        """
        Разделяет EPUB-главу по h1/h2 только тогда,
        когда большинство получившихся фрагментов имеет
        достаточный размер.

        Маленькие фрагменты объединяются с соседними.
        Если разделение создаёт слишком много маленьких глав,
        возвращается исходная глава для обычной нормализации.

        force=True позволяет использовать алгоритм в fallback,
        даже если вся книга меньше MAX_CHAPTER_CHARS.
        """

        total = chapter_char_count(unit["paragraphs"])

        if total <= MAX_CHAPTER_CHARS and not force:
            return [unit]

        # ---------------------------------------------------------
        # 1. Находим позиции крупных заголовков h1/h2
        # ---------------------------------------------------------

        heading_positions = sorted({
            position
            for position, heading in global_headings.items()
            if (
                start < position < end
                and heading["level"] <= 2
                and heading["title"]
            )
        })

        if not heading_positions:
            return [unit]

        boundaries = [start] + heading_positions + [end]

        # ---------------------------------------------------------
        # 2. Формируем предварительные фрагменты
        # ---------------------------------------------------------

        segments = []

        for index in range(len(boundaries) - 1):
            left = boundaries[index]
            right = boundaries[index + 1]

            paragraphs = list(all_paragraphs[left:right])

            if not paragraphs:
                continue

            heading = global_headings.get(left, {})

            if index == 0:
                title = unit["title"] or heading.get("title", "")
            else:
                title = heading.get("title", "")

            segments.append({
                "title": title,
                "paragraphs": paragraphs,
                "size": chapter_char_count(paragraphs),
                "origin": (
                    unit.get("origin", "technical")
                    if index == 0
                    else "heading"
                ),
            })

        if len(segments) < 2:
            return [unit]

        # ---------------------------------------------------------
        # 3. Проверяем качество предполагаемого разделения
        # ---------------------------------------------------------

        reasonable_segments = sum(
            segment["size"] >= MIN_CHAPTER_CHARS
            for segment in segments
        )

        # Требуем строгое большинство фрагментов
        # размером не менее MIN_CHAPTER_CHARS.
        if reasonable_segments * 2 <= len(segments):
            logger.info(
                (
                    "EPUB heading split skipped: "
                    "reasonable=%d total=%d chapter_title=%r"
                ),
                reasonable_segments,
                len(segments),
                unit.get("title", ""),
            )

            return [unit]

        # ---------------------------------------------------------
        # 4. Вспомогательное объединение соседних фрагментов
        # ---------------------------------------------------------

        def merge_segments(first, second):
            paragraphs = (
                first["paragraphs"] + second["paragraphs"]
            )

            return {
                "title": first["title"] or second["title"],
                "paragraphs": paragraphs,
                "size": chapter_char_count(paragraphs),
                "origin": first["origin"],
            }

        # ---------------------------------------------------------
        # 5. Объединяем слишком маленькие фрагменты
        # ---------------------------------------------------------

        merged_segments = []
        pending = None

        for segment in segments:

            if segment["size"] >= MIN_CHAPTER_CHARS:

                if pending is not None:

                    if (
                        merged_segments
                        and (
                            merged_segments[-1]["size"] + pending["size"]
                            <= MAX_CHAPTER_CHARS
                        )
                    ):
                        # Сначала пытаемся присоединить маленький
                        # фрагмент к предыдущему.
                        merged_segments[-1] = merge_segments(
                            merged_segments[-1],
                            pending,
                        )

                    else:
                        # Если предыдущая глава уже достаточно большая,
                        # присоединяем маленький фрагмент к следующей.
                        segment = merge_segments(
                            pending,
                            segment,
                        )

                    pending = None

                merged_segments.append(segment)

            else:

                if (
                    merged_segments
                    and (
                        merged_segments[-1]["size"] + segment["size"]
                        <= MAX_CHAPTER_CHARS
                    )
                ):
                    # Маленькая подглава присоединяется к предыдущей.
                    merged_segments[-1] = merge_segments(
                        merged_segments[-1],
                        segment,
                    )

                elif pending is None:
                    pending = segment

                else:
                    # Накапливаем соседние маленькие фрагменты,
                    # чтобы не создавать множество коротких глав.
                    pending = merge_segments(
                        pending,
                        segment,
                    )

        # Обрабатываем оставшийся фрагмент после последней границы.
        if pending is not None:

            if merged_segments:
                merged_segments[-1] = merge_segments(
                    merged_segments[-1],
                    pending,
                )
            else:
                merged_segments.append(pending)

        # ---------------------------------------------------------
        # 6. Формируем итоговые главы
        # ---------------------------------------------------------

        result = []

        for segment in merged_segments:

            size = segment["size"]

            result.append(
                make_chapter_unit(
                    title=segment["title"],
                    paragraphs=segment["paragraphs"],
                    protected=size >= MIN_CHAPTER_CHARS,
                    origin=segment["origin"],
                )
            )

        return result or [unit]

    def _build_epub_units(
        self,
        all_paragraphs,
        document_order,
        doc_info,
        global_headings,
        toc_entries,
    ):
        """
        Формирует главы EPUB на основе TOC и заголовков h1/h2.

        Если TOC доступен:
            - использует его границы;
            - дополнительно делит большие главы по внутренним h1/h2.

        Если TOC отсутствует или не удалось разрешить его ссылки:
            - использует h1/h2;
            - применяет те же проверки размеров;
            - при неудачном разделении оставляет обычную нормализацию.

        Если заголовки отсутствуют:
            - сохраняет документы в порядке spine.
        """

        # ---------------------------------------------------------
        # 1. Индекс документов по имени файла
        # ---------------------------------------------------------

        by_basename = {}

        for name in doc_info:
            by_basename.setdefault(
                posixpath.basename(name).lower(),
                [],
            ).append(name)

        # ---------------------------------------------------------
        # 2. Определяем позиции глав из TOC
        # ---------------------------------------------------------

        starts = []

        for index, (href, title) in enumerate(toc_entries):

            name = self._resolve_epub_document(
                href,
                doc_info,
                by_basename,
            )

            if name is None:
                logger.warning(
                    "TOC EPUB ссылается на неизвестный файл: %s",
                    href,
                )
                continue

            offset = doc_info[name]["offset"]

            local_anchors = (
                doc_info[name]["extracted"]["anchors"]
            )

            anchor = (
                unquote(
                    href.split("#", 1)[1].split("?", 1)[0]
                )
                if "#" in href
                else ""
            )

            position = offset + local_anchors.get(anchor, 0)

            starts.append((position, index, title))

        starts.sort(key=lambda item: (item[0], item[1]))

        # ---------------------------------------------------------
        # 3. Удаляем повторяющиеся позиции TOC
        # ---------------------------------------------------------

        deduplicated = []

        for position, _index, title in starts:

            if not 0 <= position < len(all_paragraphs):
                continue

            if (
                deduplicated
                and deduplicated[-1][0] == position
            ):
                deduplicated[-1] = (position, title)

            else:
                deduplicated.append((position, title))

        units = []

        # ---------------------------------------------------------
        # 4. Основной вариант: используем TOC
        # ---------------------------------------------------------

        if deduplicated:

            first_position = deduplicated[0][0]

            if first_position > 0:
                units.append(
                    make_chapter_unit(
                        "",
                        all_paragraphs[:first_position],
                        protected=False,
                        origin="technical",
                    )
                )

            for index, (start, title) in enumerate(deduplicated):

                end = (
                    deduplicated[index + 1][0]
                    if index + 1 < len(deduplicated)
                    else len(all_paragraphs)
                )

                if end <= start:
                    continue

                unit = make_chapter_unit(
                    title,
                    all_paragraphs[start:end],
                    protected=True,
                    origin="toc",
                )

                # Внутренние h1/h2 разделяют только большие главы
                # и только если получаются достаточно крупные фрагменты.
                units.extend(
                    self._split_large_epub_unit_by_headings(
                        unit,
                        start,
                        end,
                        all_paragraphs,
                        global_headings,
                    )
                )

            return units

        # ---------------------------------------------------------
        # 5. Fallback: TOC отсутствует или не удалось его разрешить
        # ---------------------------------------------------------

        major_headings = [
            (position, heading)
            for position, heading in sorted(
                global_headings.items()
            )
            if (
                heading["level"] <= 2
                and heading["title"]
            )
        ]

        if major_headings:

            # Если первый документ начинается с заголовка,
            # используем его как название первой главы.
            first_heading = global_headings.get(0, {})

            fallback_unit = make_chapter_unit(
                title=first_heading.get("title", ""),
                paragraphs=all_paragraphs,
                protected=False,
                origin="technical",
            )

            # В fallback применяем те же правила проверки размеров,
            # даже если вся книга меньше MAX_CHAPTER_CHARS.
            return self._split_large_epub_unit_by_headings(
                fallback_unit,
                0,
                len(all_paragraphs),
                all_paragraphs,
                global_headings,
                force=True,
            )

        # ---------------------------------------------------------
        # 6. Последний вариант: сохраняем документы по spine
        # ---------------------------------------------------------

        for name in document_order:

            extracted = doc_info[name]["extracted"]

            units.append(
                make_chapter_unit(
                    "",
                    extracted["paragraphs"],
                    protected=False,
                    origin="technical",
                )
            )

        return units

    # ============================================================
    # ОБЛОЖКА
    # ============================================================

    def _extract_epub_cover(self, book_epub):
        """Извлекает обложку EPUB2/EPUB3, если она присутствует."""
        try:
            cover_items = list(
                book_epub.get_items_of_type(ebooklib.ITEM_COVER)
            )
        except Exception:
            cover_items = []

        if cover_items:
            item = cover_items[0]

            return (
                item.get_content(),
                item.get_name().replace("\\", "/").split("/")[-1],
            )

        metadata = book_epub.get_metadata("OPF", "cover")

        if metadata:
            attrs = metadata[0][1] or {}
            cover_id = attrs.get("content")

            if cover_id:
                item = book_epub.get_item_with_id(cover_id)

                if item:
                    return (
                        item.get_content(),
                        item.get_name().replace("\\", "/").split("/")[-1],
                    )

        for item in book_epub.get_items_of_type(ebooklib.ITEM_IMAGE):
            if "cover" in item.get_name().lower():
                return (
                    item.get_content(),
                    item.get_name().replace("\\", "/").split("/")[-1],
                )

        return None, None
