from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    BookViewSet,
    FlashCardViewSet,
    UserProfileViewSet,
    DictionaryEntryViewSet,
    AuthViewSet,
    TranslateView,
    TranslationHistoryListView,
    TranslationDetailView,
    ConversationViewSet,
    register,
    login,
)


router = DefaultRouter()
router.register(r"books", BookViewSet, basename="book")
router.register(r"flashcards", FlashCardViewSet, basename="flashcard")
router.register(r"profile", UserProfileViewSet, basename="profile")
router.register(r"dictionary", DictionaryEntryViewSet, basename="dictionary")
router.register(r"conversations", ConversationViewSet, basename="conversation")
router.register(r"auth", AuthViewSet, basename="auth")
# router.register(r'users', UserViewSet, basename='users')


urlpatterns = [
    path("", include(router.urls)),
    path("auth/register/", views.register, name="register"),
    path("auth/login/", views.login, name="login"),
    path("translate/", TranslateView.as_view(), name="translate"),
    path("history/", TranslationHistoryListView.as_view(), name="translation-history"),
    path(
        "history/<int:pk>/", TranslationDetailView.as_view(), name="translation-detail"
    ),
]
