from django.db import migrations


def forwards(apps, schema_editor):
    WordAnalysis = apps.get_model("reader", "WordAnalysis")

    db_alias = schema_editor.connection.alias

    queryset = WordAnalysis.objects.using(db_alias).all()

    batch = []

    for analysis in queryset.iterator(chunk_size=500):
        analysis.kind = "word"

        analysis.data = {
            "lemma": analysis.lemma or "",
            "ipa": analysis.ipa or "",
            "part_of_speech": analysis.part_of_speech or "",
            "grammar": analysis.grammar or {},
            "explanation": analysis.explanation or "",
            "synonyms": analysis.synonyms or [],
            "examples": analysis.examples or [],
            "etymology": analysis.etymology or None,
        }

        batch.append(analysis)

        if len(batch) >= 500:
            WordAnalysis.objects.using(db_alias).bulk_update(
                batch,
                ["kind", "data"],
            )
            batch = []

    if batch:
        WordAnalysis.objects.using(db_alias).bulk_update(
            batch,
            ["kind", "data"],
        )


class Migration(migrations.Migration):

    dependencies = [
        ("reader", "0025_wordanalysis_data_wordanalysis_kind"),
    ]

    operations = [
        migrations.RunPython(
            forwards,
            migrations.RunPython.noop,
        ),
    ]
