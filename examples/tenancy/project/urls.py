"""Resolve the same resource paths within each selected tenant."""

from django.urls import path
from notes.views import Notes, Texts
from rest_framework.routers import SimpleRouter

router = SimpleRouter()
router.register("notes", Notes)
urlpatterns = [*router.urls, path("texts/", Texts.as_view())]
