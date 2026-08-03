import csv

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import slugify

from reader.models import DictionaryCategory, DictionaryEntry


class Command(BaseCommand):
    help = "Import dictionary CSV into DictionaryEntry. Usage: python manage.py import_dictionary /path/to/file.csv --batch 500 --dry-run"

    def add_arguments(self, parser):
        parser.add_argument("csvfile", type=str, help="Path to CSV file")
        parser.add_argument(
            "--batch", type=int, default=200, help="Report progress every N rows"
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Do not write to DB, only validate and report",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Re-import even if DictionaryEntry table already has data",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        path = options["csvfile"]
        batch_size = options["batch"]
        is_dry_run = options["dry_run"]
        is_force = options["force"]

        # Пропускаем, если данные уже есть, если явно не передан --force.
        if not is_force and DictionaryEntry.objects.exists():
            self.stdout.write(
                self.style.SUCCESS(
                    "DictionaryEntry already populated, skipping import "
                    "(use --force to re-import)."
                )
            )
            return

        if is_dry_run:
            self.stdout.write(
                self.style.WARNING(
                    "Running in --dry-run mode. No changes will be saved to the database."
                )
            )

        created_count = 0
        updated_count = 0
        skipped_count = 0

        try:
            # Используем 'with open' для автоматического закрытия файла
            with open(path, newline="", encoding="utf-8") as f:
                reader = csv.DictReader(f)

                for i, row in enumerate(reader, start=1):
                    word = (row.get("word") or "").strip()

                    if not word:
                        skipped_count += 1
                        self.stdout.write(
                            self.style.WARNING(f"Row {i}: Skipped due to empty word.")
                        )
                        continue

                    # Получаем и очищаем данные из строки CSV
                    level = (row.get("level") or "").strip() or None
                    transcription = (row.get("transcription") or "").strip()
                    definition = (row.get("definition") or "").strip()
                    raw = (row.get("raw") or "").strip()
                    category_names_raw = (row.get("categories") or "").strip()

                    self.stdout.write(
                        f"--> Processing row {i}: word='{word}', level='{level}'"
                    )

                    if is_dry_run:
                        # В режиме dry-run можно добавить больше проверок, если нужно
                        continue

                    # Создаем или обновляем основную запись
                    entry, created = DictionaryEntry.objects.update_or_create(
                        word=word,
                        level=level,  # Используем word и level как уникальный ключ
                        defaults={
                            "transcription": transcription,
                            "definition": definition,
                            "raw": raw,
                        },
                    )

                    if created:
                        created_count += 1
                    else:
                        updated_count += 1

                    # Обрабатываем категории
                    if category_names_raw:
                        category_objects = []
                        # Разделяем строку с категориями и убираем лишние пробелы
                        category_names = [
                            name.strip()
                            for name in category_names_raw.split(";")
                            if name.strip()
                        ]

                        for name in category_names:
                            # Находим или создаем категорию
                            cat, _ = DictionaryCategory.objects.get_or_create(
                                name=name, defaults={"slug": slugify(name)[:120]}
                            )
                            category_objects.append(cat)

                        # Привязываем категории к записи. .set() - правильный способ для ManyToMany
                        entry.categories.set(category_objects)

                    # Выводим прогресс
                    if i % batch_size == 0:
                        self.stdout.write(f"Processed {i} rows...")

        except FileNotFoundError:
            raise CommandError(f'File not found at: "{path}"')
        except Exception as e:
            raise CommandError(f"An error occurred: {e}")

        # Финальный отчет
        self.stdout.write(self.style.SUCCESS("-----------------------------"))
        self.stdout.write(self.style.SUCCESS("Import process finished."))
        if is_dry_run:
            self.stdout.write(
                self.style.WARNING(f"Dry run complete. Would have processed {i} rows.")
            )
        else:
            self.stdout.write(self.style.SUCCESS(f"Created: {created_count}"))
            self.stdout.write(self.style.SUCCESS(f"Updated: {updated_count}"))
        self.stdout.write(self.style.SUCCESS(f"Skipped: {skipped_count}"))
