"""
Page-number pagination used by every list endpoint.

Request:   GET /api/v1/rides/?page=2&page_size=20      (page_size max 100)
Response:  {"count": 57, "next": "<url or null>", "previous": "<url or null>", "results": [...]}

Infinite scroll in the app: keep calling `next` until it is null.
"""
from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100
