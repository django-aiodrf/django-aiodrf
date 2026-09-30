"""Django feed and sitemap views use the same public model as the API."""

from django.contrib.sitemaps import Sitemap
from django.contrib.syndication.views import Feed
from django.urls import reverse

from .models import Entry


class EntrySitemap(Sitemap):
    changefreq = "weekly"

    def items(self):
        return Entry.objects.order_by("pk")

    def location(self, obj):
        return reverse("entry-detail", args=[obj.pk])


class EntryFeed(Feed):
    title = "Example entries"
    link = "/entries/"
    description = "Public entries from the local Django example."

    def items(self):
        return Entry.objects.order_by("-pk")[:20]

    def item_title(self, item):
        return item.title

    def item_description(self, item):
        return item.title

    def item_link(self, item):
        return reverse("entry-detail", args=[item.pk])
