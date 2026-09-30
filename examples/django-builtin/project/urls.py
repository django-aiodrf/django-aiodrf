"""Django admin, authentication and content views alongside aiodrf routes."""

from demo.content import EntryFeed, EntrySitemap
from demo.views import (
    CachedValue,
    Contact,
    Entries,
    Notifications,
    SessionDetails,
    Template,
)
from django.contrib import admin
from django.contrib.flatpages import views as flatpages
from django.contrib.sitemaps.views import sitemap
from django.urls import include, path
from rest_framework.routers import SimpleRouter

router = SimpleRouter()
router.register("entries", Entries)
urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("django.contrib.auth.urls")),
    path("pages/<path:url>", flatpages.flatpage),
    path("cache/", CachedValue.as_view()),
    path("contact/", Contact.as_view()),
    path("signals/", Notifications.as_view()),
    path("session/", SessionDetails.as_view()),
    path("template/", Template.as_view()),
    path("sitemap.xml", sitemap, {"sitemaps": {"entries": EntrySitemap}}),
    path("feed/", EntryFeed()),
    *router.urls,
]
