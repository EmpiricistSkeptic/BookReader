"""
Общая логика работы с главами: нормализация и нарезка.

Не зависит ни от формата книги, ни от Django.
Единица главы (unit) — словарь:
    {"title": str, "paragraphs": list[str], "protected": bool, "origin": str}
"""
import logging

from .exceptions import BookParseError

logger = logging.getLogger(__name__)

MIN_CHAPTER_CHARS = 3_000
MAX_CHAPTER_CHARS = 35_000


def make_chapter_unit(
    title,
    paragraphs,
    protected=False,
    origin="technical",
):
    """Внутреннее представление фрагмента книги до сохранения в БД."""
    return {
        "title": (title or "").strip(),
        "paragraphs": [
            paragraph.strip()
            for paragraph in paragraphs
            if paragraph and paragraph.strip()
        ],
        "protected": bool(protected),
        "origin": origin,
    }


def chapter_char_count(paragraphs):
    return sum(len(paragraph) for paragraph in paragraphs)


def split_large_chapter(unit):
    """
    Равномерно разделяет большую главу по границам абзацев.

    - Определяет необходимое количество частей.
    - Старается распределить текст равномерно.
    - Не превышает MAX_CHAPTER_CHARS, если это возможно
      без разрыва отдельных абзацев.
    - Если последняя часть меньше 30% целевого размера,
      перераспределяет последние две части.
    - Не разрывает одиночные абзацы.
    """

    paragraphs = unit["paragraphs"]
    total = chapter_char_count(paragraphs)

    max_size = MAX_CHAPTER_CHARS

    if total <= max_size or len(paragraphs) < 2:
        return [unit]

    # ---------------------------------------------------------
    # 1. Определяем количество частей
    # ---------------------------------------------------------

    group_count = min(
        len(paragraphs),
        (total + max_size - 1) // max_size,
    )

    if group_count < 2:
        return [unit]

    target_size = total / group_count

    # ---------------------------------------------------------
    # 2. Создаём накопительные размеры абзацев
    # ---------------------------------------------------------

    prefix_sizes = [0]

    for paragraph in paragraphs:
        prefix_sizes.append(
            prefix_sizes[-1] + len(paragraph)
        )

    # ---------------------------------------------------------
    # 3. Равномерно распределяем абзацы
    # ---------------------------------------------------------

    groups = []
    start = 0
    paragraph_count = len(paragraphs)

    for group_index in range(group_count - 1):

        remaining_groups = (
            group_count - group_index - 1
        )

        # Обязательно оставляем хотя бы один абзац
        # для каждой оставшейся части.
        max_end = paragraph_count - remaining_groups

        candidate_ends = list(
            range(start + 1, max_end + 1)
        )

        # Если возможно, выбираем только такие границы,
        # при которых текущая часть не превышает MAX.
        feasible_ends = [
            end
            for end in candidate_ends
            if (
                prefix_sizes[end] - prefix_sizes[start]
                <= max_size
            )
        ]

        if feasible_ends:
            candidate_ends = feasible_ends

        # Определяем желаемый размер части с учётом
        # оставшегося текста и количества оставшихся частей.
        remaining_size = (
            prefix_sizes[-1] - prefix_sizes[start]
        )

        desired_size = (
            remaining_size / (remaining_groups + 1)
        )

        # Выбираем границу, наиболее близкую к целевому размеру.
        end = min(
            candidate_ends,
            key=lambda position: abs(
                (prefix_sizes[position] - prefix_sizes[start])
                - desired_size
            ),
        )

        groups.append(paragraphs[start:end])
        start = end

    # Последняя часть получает оставшиеся абзацы.
    groups.append(paragraphs[start:])

    if len(groups) <= 1:
        return [unit]

    # ---------------------------------------------------------
    # 4. Проверяем размер последней части
    # ---------------------------------------------------------

    last_size = chapter_char_count(groups[-1])

    min_last_size = target_size * 0.30

    if (
        len(groups) >= 2
        and last_size < min_last_size
    ):
        # Объединяем последние две части временно.
        combined = groups[-2] + groups[-1]

        combined_prefix = [0]

        for paragraph in combined:
            combined_prefix.append(
                combined_prefix[-1] + len(paragraph)
            )

        candidate_splits = list(
            range(1, len(combined))
        )

        # Предпочитаем варианты, где обе части
        # не превышают установленный максимум.
        feasible_splits = [
            split
            for split in candidate_splits
            if (
                combined_prefix[split] <= max_size
                and (
                    combined_prefix[-1]
                    - combined_prefix[split]
                ) <= max_size
            )
        ]

        if feasible_splits:
            candidate_splits = feasible_splits

        # Находим наиболее равномерную границу.
        best_split = min(
            candidate_splits,
            key=lambda split: abs(
                combined_prefix[split]
                - (
                    combined_prefix[-1]
                    - combined_prefix[split]
                )
            ),
        )

        groups[-2] = combined[:best_split]
        groups[-1] = combined[best_split:]

        logger.info(
            (
                "Rebalanced last EPUB/FB2 chapter parts: "
                "previous=%d last=%d"
            ),
            chapter_char_count(groups[-2]),
            chapter_char_count(groups[-1]),
        )

    # ---------------------------------------------------------
    # 5. Создаём итоговые главы
    # ---------------------------------------------------------

    result = []
    base_title = unit["title"] or "Chapter"

    total_groups = len(groups)

    for index, group in enumerate(groups, 1):

        result.append(
            make_chapter_unit(
                title=(
                    f"{base_title} — part "
                    f"{index}/{total_groups}"
                ),
                paragraphs=group,
                protected=unit["protected"],
                origin=unit["origin"],
            )
        )

    return result


def normalize_chapter_units(units):
    """
    Объединяет только небольшие технические фрагменты.
    Главы, заданные TOC или авторскими заголовками, защищены от склейки.
    Затем делит чрезмерно большие главы.

    Возвращает список {"title", "content", "order"}.
    """
    prepared = []

    for unit in units:
        paragraphs = [
            paragraph.strip()
            for paragraph in unit.get("paragraphs", [])
            if paragraph and paragraph.strip()
        ]

        if not paragraphs:
            continue

        current = make_chapter_unit(
            title=unit.get("title", ""),
            paragraphs=paragraphs,
            protected=unit.get("protected", False),
            origin=unit.get("origin", "technical"),
        )

        size = chapter_char_count(current["paragraphs"])

        if (
            not current["protected"]
            and size < MIN_CHAPTER_CHARS
            and prepared
        ):
            previous = prepared[-1]
            combined_size = (
                chapter_char_count(previous["paragraphs"]) + size
            )

            if combined_size <= MAX_CHAPTER_CHARS:
                previous["paragraphs"].extend(current["paragraphs"])
                continue

        prepared.append(current)

    normalized = []

    for unit in prepared:
        normalized.extend(split_large_chapter(unit))

    result = []

    for index, unit in enumerate(normalized, 1):
        title = (
            unit["title"]
            or f"Chapter {index}"
        )[:500]

        result.append(
            {
                "title": title,
                "content": "\n\n".join(unit["paragraphs"]),
                "order": index,
            }
        )

    if not result:
        raise BookParseError("В книге не удалось найти текст.")

    return result
