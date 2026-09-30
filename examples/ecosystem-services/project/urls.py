"""Local task/storage examples and an explicitly configured search endpoint."""

from demo.views import (
    CachedPage,
    CachedValue,
    DjangoSearch,
    Jobs,
    OpenSearchJobs,
    Search,
    Storage,
)
from django.urls import path
from rest_framework.routers import SimpleRouter

router = SimpleRouter()
router.register("jobs", Jobs)
urlpatterns = [
    *router.urls,
    path("storage/", Storage.as_view()),
    path("search/", Search.as_view()),
    path("search/django/", DjangoSearch.as_view()),
    path("cache/", CachedValue.as_view()),
    path("cache-page/", CachedPage.as_view()),
    path("opensearch/", OpenSearchJobs.as_view()),
]
