"""Single pagination class reused by every list endpoint from Volume 10
onward, so page-size behaviour is identical across the whole API instead
of each viewset picking its own default."""

from rest_framework.pagination import PageNumberPagination


class StandardResultsPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = "page_size"
    max_page_size = 100
