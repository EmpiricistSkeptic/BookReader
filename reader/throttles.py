from rest_framework.throttling import UserRateThrottle, AnonRateThrottle


class TranslationThrottle(UserRateThrottle):
    scope = "translation"

class AuthThrottle(AnonRateThrottle):
    scope = "auth"
