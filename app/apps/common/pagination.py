from collections import OrderedDict

from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response


class StandardPageNumberPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(self, data):
        payload = OrderedDict(
            [
                ("success", True),
                ("code", "OK"),
                ("message", "Operation completed successfully"),
                (
                    "data",
                    OrderedDict(
                        [
                            ("count", self.page.paginator.count),
                            ("page", self.page.number),
                            ("page_size", len(data)),
                            ("total_pages", self.page.paginator.num_pages),
                            ("next", self.get_next_link()),
                            ("previous", self.get_previous_link()),
                            ("results", data),
                        ]
                    ),
                ),
            ]
        )
        request_id = getattr(self.request, "request_id", None)
        if request_id:
            payload["request_id"] = request_id
        return Response(payload)
