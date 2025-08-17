from django.core.management.base import BaseCommand
from django.db.models import Count
from reader.models import DictionaryEntry # Убедитесь, что путь к модели верный

class Command(BaseCommand):
    help = 'Finds and lists all duplicate words in the DictionaryEntry model.'

    def handle(self, *args, **options):
        self.stdout.write("Searching for duplicate words...")

        # 1. Группируем записи по полю 'word' и считаем, сколько раз каждое слово встречается
        #    values() - группирует, annotate() - добавляет вычисляемое поле (в нашем случае, счетчик)
        duplicates = (
            DictionaryEntry.objects.values("word")
            .annotate(word_count=Count("word"))
            .filter(word_count__gt=1) # Оставляем только те, что встречаются больше одного раза
            .order_by("-word_count") # Сортируем, чтобы самые частые дубликаты были вверху
        )

        if not duplicates:
            self.stdout.write(self.style.SUCCESS("No duplicate words found. The database is clean."))
            return

        self.stdout.write(self.style.WARNING(f"Found {len(duplicates)} words with duplicate entries:"))
        self.stdout.write("------------------------------------")

        # 2. Проходим по каждому найденному слову-дубликату
        for item in duplicates:
            word = item["word"]
            count = item["word_count"]
            self.stdout.write(self.style.WARNING(f'\n--- Word: "{word}" (Found {count} times) ---'))

            # 3. Находим и выводим все реальные записи для этого слова
            entries = DictionaryEntry.objects.filter(word=word).order_by("id")
            for entry in entries:
                self.stdout.write(
                    f"  - ID: {entry.id}, Level: {entry.level}, Definition: '{entry.definition[:50]}...'"
                )

        self.stdout.write("\n------------------------------------")
        self.stdout.write(self.style.SUCCESS("Search complete."))