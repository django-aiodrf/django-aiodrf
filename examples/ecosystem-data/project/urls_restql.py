"""Route the optional RESTQL representation beside the ordinary data examples."""

from demo.restql import Books
from django.urls import path

from .urls import urlpatterns as ordinary_patterns

urlpatterns = [*ordinary_patterns, path("restql/", Books.as_view({"get": "list"}))]
