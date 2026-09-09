from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reader", "0019_book_updated_at"),
    ]

    operations = [
        migrations.AlterField(
            model_name="book",
            name="updated_at",
            field=models.DateTimeField(auto_now=True),
        ),
    ]