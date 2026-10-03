"""Schema-backed serializers and their validation contracts. Example: bookshop."""

from aiodrf_asgi_lifespan.asgi import get_lifespan_state
from rest_framework import serializers

from aiodrf.contrib.builtin.list_prefetch import PrefetchListSerializer
from bookshop.lifecycle import Resources

from .models import Author, Book


class BookListSerializer(PrefetchListSerializer):
    """Asks the stock service once per page, not once per book."""

    async def aprefetch(self, instances):
        stock = get_lifespan_state(self.context["request"], Resources).stock
        response = await stock.post("/levels/", json=[book.sku for book in instances])
        response.raise_for_status()
        levels = response.json()
        for book in instances:
            book.in_stock = levels.get(book.sku, 0)


class BookSerializer(serializers.ModelSerializer):
    author = serializers.SlugRelatedField(
        slug_field="name", queryset=Author.objects.all()
    )
    # Set by BookListSerializer.aprefetch; a single book is not looked up.
    in_stock = serializers.SerializerMethodField()

    class Meta:
        model = Book
        fields = ["id", "sku", "title", "author", "in_stock", "updated"]
        list_serializer_class = BookListSerializer
        # aiodrf derives select_related("author") from the fields.
        auto_prefetch = True

    def get_in_stock(self, book) -> int | None:
        return getattr(book, "in_stock", None)


class ExportRow(serializers.Serializer):
    """One line of the NDJSON export."""

    sku = serializers.CharField()
    title = serializers.CharField()
    author = serializers.CharField(source="author.name")


class SearchParameters(serializers.Serializer):
    q = serializers.CharField(min_length=2)
    limit = serializers.IntegerField(min_value=1, max_value=50, default=10)


class SearchResults(serializers.Serializer):
    titles = serializers.ListField(child=serializers.CharField())
