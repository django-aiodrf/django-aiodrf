"""Explicit routes for each serializer, router and renderer contract."""

from demo import formats, views
from django.urls import include, path
from rest_framework.routers import SimpleRouter
from rest_framework_nested.routers import NestedSimpleRouter

router = SimpleRouter()
router.register("authors", views.Authors)
router.register("books", views.Books)
router.register("filtered-books", views.FilteredBooks, basename="filtered-book")
router.register("nested-books", views.NestedBooks, basename="nested-book")
router.register("expanded-books", views.ExpandedBooks, basename="expanded-book")
router.register("photos", views.Photos)
router.register("notes", views.Notes)
router.register("projects", views.Projects)
router.register("jsonapi/books", formats.JSONAPIBooks, basename="jsonapi-book")
nested = NestedSimpleRouter(router, "authors", lookup="author")
nested.register("books", views.AuthorBooks, basename="author-book")
urlpatterns = [
    path("address/", views.AddressEcho.as_view()),
    path("multipart/", views.MultipartSummary.as_view()),
    path("camel-case/", formats.CamelCaseEcho.as_view()),
    path("orjson/", formats.FastJSONBooks.as_view()),
    path("spreadsheet/", formats.SpreadsheetBooks.as_view()),
    path("table/", formats.TableBooks.as_view()),
    path("uncounted/", formats.UncountedBooks.as_view()),
    path("", include(router.urls)),
    path("", include(nested.urls)),
]
