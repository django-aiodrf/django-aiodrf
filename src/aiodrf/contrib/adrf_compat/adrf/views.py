"""adrf's ``views``: aiodrf's ``APIView`` with adrf's members."""

from aiodrf import views
from aiodrf.contrib.adrf_compat._views import AdrfViewMixin

__all__ = ["APIView"]


class APIView(AdrfViewMixin, views.APIView):
    pass
