import logging

from django.db import IntegrityError, transaction

from ...models import WordAnalysis
from .deepseek import DeepSeekTextAnalyzer


logger = logging.getLogger(__name__)


# Existing legacy columns are kept during the first deployment. Populate them
# so non-nullable old columns do not block creating expression/sentence rows.
# They are removed only in a later migration after the JSONField rollout.
LEGACY_FIELD_DEFAULTS = {
    "lemma": "",
    "ipa": "",
    "part_of_speech": "",
    "grammar": {},
    "explanation": "",
    "synonyms": [],
    "examples": [],
    "etymology": "",
}


class TextAnalysisService:
    """Generate and cache one analysis for a Translation (OneToOne relation)."""

    @staticmethod
    def _is_stale(analysis: WordAnalysis) -> bool:
        """Rows that must be regenerated.

        - empty data (row created by the old code before the data migration ran);
        - sentence rows saved by the first release as {"status": "not_supported"},
          before sentence analysis existed.
        """
        data = analysis.data or {}
        return not data or data.get("status") == "not_supported"

    @staticmethod
    def _legacy_defaults(result: dict) -> dict:
        model_fields = {field.name for field in WordAnalysis._meta.concrete_fields}
        defaults = {}

        for field_name, empty_value in LEGACY_FIELD_DEFAULTS.items():
            if field_name not in model_fields:
                continue

            if result["kind"] == "word":
                defaults[field_name] = result["data"].get(field_name, empty_value)
            else:
                defaults[field_name] = empty_value

        return defaults

    @staticmethod
    def _apply_result(analysis: WordAnalysis, result: dict) -> WordAnalysis:
        analysis.kind = result["kind"]
        analysis.data = result["data"]

        legacy_defaults = TextAnalysisService._legacy_defaults(result)
        for field_name, value in legacy_defaults.items():
            setattr(analysis, field_name, value)

        update_fields = ["kind", "data", *legacy_defaults.keys()]
        if any(field.name == "updated_at" for field in WordAnalysis._meta.concrete_fields):
            update_fields.append("updated_at")

        analysis.save(update_fields=update_fields)
        return analysis

    def get_or_generate(self, translation: object) -> WordAnalysis:
        try:
            existing = translation.word_analysis
        except WordAnalysis.DoesNotExist:
            existing = None

        if existing is not None and not self._is_stale(existing):
            return existing

        result = DeepSeekTextAnalyzer().analyze(translation)

        if existing is not None:
            # Stale row: overwrite it instead of creating a second one.
            return self._apply_result(existing, result)

        defaults = {
            "kind": result["kind"],
            "data": result["data"],
            **self._legacy_defaults(result),
        }

        try:
            with transaction.atomic():
                analysis, created = WordAnalysis.objects.get_or_create(
                    translation=translation,
                    defaults=defaults,
                )
        except IntegrityError:
            # A parallel request may have inserted the OneToOne row after this
            # request checked for an existing analysis.
            analysis = WordAnalysis.objects.get(translation=translation)
            created = False

        if not created and self._is_stale(analysis):
            analysis = self._apply_result(analysis, result)

        return analysis