from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView

from aiodrf.routers import DefaultRouter
from tests.testapp import views

router = DefaultRouter()
router.register("books", views.BookViewSet)
router.register("nested-books", views.NestedBookViewSet, basename="nested-book")
router.register("hook-books", views.AsyncHookBookViewSet, basename="hook-book")
router.register("legacy-books", views.LegacyBookViewSet, basename="legacy-book")

urlpatterns = [
    path("", include(router.urls)),
    path("authors/", views.AuthorListView.as_view()),
    path("guarded/", views.GuardedView.as_view()),
    path("mixed/", views.MixedView.as_view()),
    path("whoami/", views.WhoAmIView.as_view()),
    path("throttled/", views.ThrottledView.as_view()),
    path("fn/async/", views.async_function_view),
    path("fn/sync/", views.sync_function_view),
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
]
