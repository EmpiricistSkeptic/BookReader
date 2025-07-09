from rest_framework.throttling import UserRateThrottle


class TranslationThrottle(UserRateThrottle):
    scope = "translation"
