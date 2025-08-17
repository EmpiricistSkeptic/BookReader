from django.core.management.base import BaseCommand
from django.db.models import Count
from django.db.models.functions import Lower, Trim
from reader.models import DictionaryEntry

class Command(BaseCommand):
    help = 'Finds words that are duplicates when ignoring case and trailing spaces.'

    def handle(self, *args, **options):
        self.stdout.write("Searching for collation duplicates (case-insensitive, trimmed)...")

        # 1. Создаем "нормализованное" поле: переводим в нижний регистр и убираем пробелы
        #    Затем группируем по этому новому полю и считаем
        duplicates = (
            DictionaryEntry.objects.annotate(
                word_normalized=Trim(Lower("word"))
            )
            .values("word_normalized")
            .annotate(normalized_count=Count("id"))
            .filter(normalized_count__gt=1)
            .order_by("-normalized_count")
        )

        if not duplicates:
            self.stdout.write(self.style.SUCCESS("No collation duplicates found. This is very strange."))
            return

        self.stdout.write(self.style.WARNING(f"Found {len(duplicates)} groups of collation duplicates:"))
        self.stdout.write("------------------------------------")

        # 2. Проходим по каждой найденной группе
        for group in duplicates:
            normalized_word = group["word_normalized"]
            count = group["normalized_count"]
            self.stdout.write(
                self.style.WARNING(f'\n--- Group "{normalized_word}" (Found {count} variants) ---')
            )

            # 3. Находим и выводим все реальные записи для этой группы
            entries = DictionaryEntry.objects.annotate(
                word_normalized=Trim(Lower("word"))
            ).filter(word_normalized=normalized_word).order_by("id")
            
            for entry in entries:
                self.stdout.write(f"  - ID: {entry.id}, Word: '{entry.word}'")

        self.stdout.write("\n------------------------------------")
        self.stdout.write(self.style.SUCCESS("Search complete. These are the words causing unstable sorting."))