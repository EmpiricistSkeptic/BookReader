from django.utils import timezone
from django_filters import rest_framework as filters

from .models import FlashCard


class FlashCardFilter(filters.FilterSet):
    # Добавляем кастомный фильтр "is_due"
    # Он будет принимать true/false и фильтровать карточки "на сегодня"
    is_due = filters.BooleanFilter(method="filter_is_due", label="Is Due for Review")

    is_learning = filters.BooleanFilter(field_name="is_learning")

    class Meta:
        model = FlashCard
        fields = ["is_due", "is_learning"]

    def filter_is_due(self, queryset, name, value):
        # Этот метод вызывается, когда в запросе есть /?is_due=true
        if value:
            return queryset.filter(next_review__lte=timezone.now())
        return queryset
