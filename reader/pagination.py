from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class TranslationPagination(PageNumberPagination):
    page_size = 20
    page_size_query_params = "per_page"
    max_page_size = 100
    page_query_param = "page"

    def paginated_response(self, data):
        return Response(
            {
                "translations": data,
                "total_count": self.page.paginator.count,
                "page": self.page.number,
                "per_page": self.get_page_size(self.request),
                "has_next": self.page.has_next(),
                "has_previous": self.page.has_previous(),
            }
        )
