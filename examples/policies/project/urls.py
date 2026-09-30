"""URL routing for the policies application."""

from demo.views import urlpatterns as api_urls
from django.urls import path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    *api_urls,
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="schema")),
]

__all__ = ["urlpatterns"]
