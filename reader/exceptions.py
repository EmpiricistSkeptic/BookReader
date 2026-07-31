class AppServiceError(Exception):
    pass

class TranslationServiceError(AppServiceError):
    pass

class AIServiceError(AppServiceError):
    pass

