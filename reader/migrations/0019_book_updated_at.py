from django.db import migrations, models


def set_updated_at_from_uploaded_at(apps, schema_editor):
    Book = apps.get_model("reader", "Book")
    Book.objects.filter(updated_at__isnull=True).update(
        updated_at=models.F("uploaded_at")
    )


class Migration(migrations.Migration):

    dependencies = [
        ("reader", "0018_conversation_mode"),
    ]

    operations = [
        migrations.AddField(
            model_name="book",
            name="updated_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
        migrations.RunPython(
            set_updated_at_from_uploaded_at,
            migrations.RunPython.noop,
        ),
    ]