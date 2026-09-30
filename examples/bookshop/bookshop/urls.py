"""URL routing for the bookshop application."""

from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView
from rest_framework.routers import DefaultRouter

from catalog import views

router = DefaultRouter()
router.register("books", views.BookViewSet)

urlpatterns = [
    path("", include(router.urls)),
    path("search/", views.BookSearch.as_view(), name="book-search"),
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
]
