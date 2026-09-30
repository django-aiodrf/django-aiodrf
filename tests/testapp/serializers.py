from rest_framework import serializers as drf_serializers

from aiodrf import serializers
from tests.testapp.models import Author, Book, Tag


class AuthorSerializer(drf_serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]


class TagSerializer(drf_serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ["id", "name"]


class BookSerializer(drf_serializers.ModelSerializer):
    """A plain DRF serializer: relations and unique validation hit the database."""

    class Meta:
        model = Book
        fields = ["id", "title", "isbn", "pages", "author", "tags"]


class NestedBookSerializer(serializers.ModelSerializer):
    author = AuthorSerializer()
    tags = TagSerializer(many=True)
    author_name = serializers.CharField(source="author.name")

    class Meta:
        model = Book
        fields = ["id", "title", "author", "tags", "author_name"]
        auto_prefetch = True


class AsyncHookBookSerializer(serializers.ModelSerializer):
    summary = serializers.SerializerMethodField()
    method_summary = serializers.CharField(source="asummary", read_only=True)
    sync_method = serializers.SerializerMethodField()

    class Meta:
        model = Book
        fields = [
            "id",
            "title",
            "isbn",
            "pages",
            "author",
            "tags",
            "summary",
            "method_summary",
            "sync_method",
        ]

    async def validate_title(self, value):
        if await Book.objects.filter(title__iexact=value).aexists():
            raise serializers.ValidationError("A book with this title already exists.")
        return value.strip()

    async def validate(self, attrs):
        if attrs.get("pages", 100) > 5000:
            raise serializers.ValidationError("Too long.")
        return attrs

    async def get_summary(self, obj):
        return await obj.asummary()

    def get_sync_method(self, obj):
        return obj.title.upper()
