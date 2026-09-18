import logging


from .deepseek import DeepSeekWordAnalyzer
from ...models import WordAnalysis


logger = logging.getLogger(__name__)


class WordAnalysisService:
    def get_or_generate(self, translation):
        try:
            return translation.word_analysis
        except WordAnalysis.DoesNotExist:
            pass

        analyzer = DeepSeekWordAnalyzer()

        data = analyzer.analyze(translation)

        return WordAnalysis.objects.create(
            translation=translation,
            **data,
        )
