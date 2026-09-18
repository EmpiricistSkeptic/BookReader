from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AuthViewSet,
    BookViewSet,
    ConversationViewSet,
    DictionaryCategoryListView,
    DictionaryEntryViewSet,
    FlashCardViewSet,
    TranslationViewSet,
    UserProfileViewSet,
)

router = DefaultRouter()
router.register(r"books", BookViewSet, basename="book")
router.register(r"flashcards", FlashCardViewSet, basename="flashcard")
router.register(r"profile", UserProfileViewSet, basename="profile")
router.register(r"dictionary", DictionaryEntryViewSet, basename="dictionary")
router.register(r"conversations", ConversationViewSet, basename="conversation")
router.register(r"translations", TranslationViewSet, basename="translation")
router.register(r"auth", AuthViewSet, basename="auth")
router.register(r"dictionary", DictionaryEntryViewSet, basename="dictionary-entry")
# router.register(r'users', UserViewSet, basename='users')


urlpatterns = [
    path("", include(router.urls)),
    path("categories/", DictionaryCategoryListView.as_view(), name="category-list"),
]
