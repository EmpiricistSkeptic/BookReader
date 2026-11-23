from rest_framework.throttling import UserRateThrottle, AnonRateThrottle


class TranslationThrottle(UserRateThrottle):
    scope = "translation"

class AuthThrottle(AnonRateThrottle):
    scope = "auth"

class WordAnalysisThrottle(UserRateThrottle):
    scope = "word_analysis"

class WordAnalysisBurstThrottle(UserRateThrottle):
    scope = "word_analysis_burst"

