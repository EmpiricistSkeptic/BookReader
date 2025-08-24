import unicodedata

from django.core.management.base import BaseCommand

from reader.models import DictionaryEntry


class Command(BaseCommand):
    help = "Inspects every word for hidden characters or inconsistencies."

    def handle(self, *args, **options):
        self.stdout.write(
            self.style.SUCCESS("Starting deep inspection of all dictionary words...")
        )
        self.stdout.write(
            "Format: ID | 'Word' | Byte Representation | Unicode Name of each character"
        )
        self.stdout.write(
            "---------------------------------------------------------------------"
        )

        all_entries = DictionaryEntry.objects.all().order_by("word", "id")

        found_issues = 0

        for entry in all_entries:
            # Преобразуем слово в безопасное представление, которое покажет все скрытые символы
            repr_word = repr(entry.word)

            # Проверяем на наличие "подозрительных" символов (не стандартные ASCII буквы)
            is_suspicious = not entry.word.isascii() or entry.word.strip() != entry.word

            # Если слово подозрительное, выводим его с подсветкой
            if is_suspicious:
                found_issues += 1
                style = self.style.WARNING
            else:
                style = self.style.SUCCESS

            # Выводим подробную информацию
            self.stdout.write(style(f"ID: {entry.id:<5} | Word: {repr_word:<25}"))

            # Выводим детальную информацию по каждому символу, если есть подозрения
            if is_suspicious:
                for char in entry.word:
                    try:
                        char_name = unicodedata.name(char)
                    except ValueError:
                        char_name = "UNKNOWN CHARACTER"
                    self.stdout.write(style(f"    '{char}' -> {char_name}"))

        self.stdout.write(
            "---------------------------------------------------------------------"
        )
        if found_issues > 0:
            self.stdout.write(
                self.style.ERROR(
                    f"Inspection complete. Found {found_issues} suspicious entries."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "Inspection complete. No suspicious characters found in words."
                )
            )
