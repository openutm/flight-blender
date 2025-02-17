import math

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class StandardResultsSetPagination(PageNumberPagination):
    page = 1
    page_size = 10
    page_size_query_param = "page_size"

    def get_paginated_response(self, data):
        """
        Returns a paginated response with the given data.

        Args:
            data (list): The data to be paginated.

        Returns:
            Response: A DRF Response object containing paginated data and metadata.
        """
        return Response(
            {
                "links": {
                    "next": self.get_next_link(),
                    "previous": self.get_previous_link(),
                },
                "total": self.page.paginator.count,
                "page": math.ceil(int(self.request.GET.get("page", 1))),  # cannot set default = self.page
                "pages": int(math.ceil(self.page.paginator.count / self.page_size)),
                "page_size": int(self.request.GET.get("page_size", self.page_size)),
                "results": data,
            }
        )
