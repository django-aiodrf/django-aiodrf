"""
Representation of loaded objects on the event loop in thread mode.

``REPRESENTATION_MODE = "thread"`` runs DRF's representation in a worker
because it may query. When the serializer reads only columns the objects have
loaded and relations ``select_related``/``prefetch_related`` cached, it cannot,
and aiodrf represents on the loop: the same output, without the hop.
"""

import datetime
import uuid

import pytest
from asgiref.sync import sync_to_async
from django.db.models import Prefetch
from django.test.utils import isolate_apps
from rest_framework import serializers as drf

from aiodrf import aio, serializers
from aiodrf.aio._loaded import reads_loaded
from aiodrf.test import count_hops
from tests.testapp.models import Attachment, Author, Book, Edition, Tag

pytestmark = pytest.mark.django_db(transaction=True)


class AuthorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]


class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ["id", "name"]


class BookSerializer(serializers.ModelSerializer):
    author = AuthorSerializer()
    tags = TagSerializer(many=True)

    class Meta:
        model = Book
        fields = ["id", "title", "pages", "author", "tags"]


class Translated(serializers.ModelSerializer):
    translator = AuthorSerializer(allow_null=True)

    class Meta:
        model = Edition
        fields = ["id", "translator"]


class AuthorBooks(serializers.ModelSerializer):
    class Titles(serializers.ModelSerializer):
        class Meta:
            model = Book
            fields = ["id", "title"]

    books = Titles(many=True)

    class Meta:
        model = Author
        fields = ["id", "name", "books"]


class Method(serializers.ModelSerializer):
    shout = serializers.SerializerMethodField()

    class Meta:
        model = Author
        fields = ["id", "shout"]

    def get_shout(self, author):
        return author.name.upper()


class Overridden(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]

    def to_representation(self, instance):
        return super().to_representation(instance)


@pytest.fixture
def books():
    ada = Author.objects.create(name="Ada")
    red, blue = Tag.objects.create(name="red"), Tag.objects.create(name="blue")
    for index in range(3):
        book = Book.objects.create(title=f"B{index}", isbn=f"i{index}", author=ada)
        book.tags.set([red, blue])
        Edition.objects.create(
            code=uuid.uuid4(),
            book=book,
            published=datetime.datetime.now(datetime.UTC),
            price="1.50",
            format="pb",
        )
    return ada


async def represent(serializer):
    """aiodrf's data and its hops, and DRF's for the same objects."""
    with count_hops() as hops:
        data = await aio.data(serializer)
    return data, hops.count


def drf_data(serializer_class, instance, **kwargs):
    return sync_to_async(lambda: serializer_class(instance, **kwargs).data)()


async def assert_represented(serializer_class, instance, hops, **kwargs):
    data, counted = await represent(serializer_class(instance, **kwargs))
    assert counted == hops
    assert data == await drf_data(serializer_class, instance, **kwargs)


async def test_loaded_objects_are_represented_on_the_loop(books):
    authors = [author async for author in Author.objects.all()]
    await assert_represented(AuthorSerializer, authors, 0, many=True)
    await assert_represented(AuthorSerializer, authors[0], 0)


async def test_cached_relations_are_represented_on_the_loop(books):
    queryset = Book.objects.select_related("author").prefetch_related("tags")
    loaded = [book async for book in queryset]
    await assert_represented(BookSerializer, loaded, 0, many=True)
    await assert_represented(BookSerializer, loaded[0], 0)
    authors = [a async for a in Author.objects.prefetch_related("books")]
    await assert_represented(AuthorBooks, authors, 0, many=True)


async def test_a_null_relation_needs_no_query(books):
    loaded = [edition async for edition in Edition.objects.all()]
    assert loaded[0].translator_id is None
    await assert_represented(Translated, loaded, 0, many=True)


@pytest.mark.aiodrf_settings(REPRESENTATION_MODE="thread")
@pytest.mark.parametrize(
    "queryset",
    [
        pytest.param(lambda: Book.objects.prefetch_related("tags"), id="author"),
        pytest.param(lambda: Book.objects.select_related("author"), id="tags"),
        pytest.param(
            lambda: (
                Book.objects.select_related("author")
                .prefetch_related("tags")
                .defer("title")
            ),
            id="deferred column",
        ),
        pytest.param(
            lambda: (
                Book.objects.select_related("author")
                .prefetch_related("tags")
                .only("id", "pages", "author")
            ),
            id="only",
        ),
        pytest.param(
            lambda: Book.objects.select_related("author").prefetch_related(
                Prefetch("tags", to_attr="tag_list")
            ),
            id="prefetched elsewhere",
        ),
    ],
)
async def test_what_may_query_keeps_the_worker(books, queryset):
    loaded = [book async for book in queryset()]
    await assert_represented(BookSerializer, loaded, 1, many=True)


async def test_unevaluated_querysets_keep_the_worker(books):
    await assert_represented(AuthorSerializer, Author.objects.all(), 1, many=True)


@pytest.mark.aiodrf_settings(
    REPRESENTATION_MODE="thread", SERIALIZER_BACKEND_FALLBACK="drf"
)
@pytest.mark.parametrize("serializer_class", [Method, Overridden])
async def test_code_of_the_projects_keeps_the_worker(books, serializer_class):
    authors = [author async for author in Author.objects.all()]
    await assert_represented(serializer_class, authors, 1, many=True)


@pytest.mark.aiodrf_settings(
    REPRESENTATION_MODE="thread", SERIALIZER_BACKEND_FALLBACK="drf"
)
async def test_a_field_changed_on_the_instance_keeps_the_worker(books):
    authors = [author async for author in Author.objects.all()]
    serializer = AuthorSerializer(authors, many=True)
    serializer.child.fields["name"] = drf.SerializerMethodField()
    serializer.child.fields["name"].bind("name", serializer.child)
    serializer.child.get_name = lambda author: author.name
    data, hops = await represent(serializer)
    assert hops == 1
    assert data == [{"id": a.id, "name": a.name} for a in authors]


@pytest.mark.aiodrf_settings(REPRESENTATION_MODE="thread")
async def test_a_proxy_or_subclass_instance_keeps_the_worker(books):
    class Pen(Author):
        class Meta:
            proxy = True
            app_label = "testapp"

    authors = [author async for author in Pen.objects.all()]
    await assert_represented(AuthorSerializer, authors, 1, many=True)


class Keyed(serializers.ModelSerializer):
    class Meta:
        model = Book
        fields = ["id", "title", "author"]


@pytest.mark.aiodrf_settings(REPRESENTATION_MODE="thread")
async def test_a_primary_key_relation_reads_its_loaded_column(books):
    loaded = [book async for book in Book.objects.all()]
    await assert_represented(Keyed, loaded, 0, many=True)
    deferred = [book async for book in Book.objects.defer("author")]
    await assert_represented(Keyed, deferred, 1, many=True)


class Files(serializers.ModelSerializer):
    class Meta:
        model = Attachment
        fields = ["id", "file"]


@pytest.mark.aiodrf_settings(
    REPRESENTATION_MODE="thread", SERIALIZER_BACKEND_FALLBACK="drf"
)
async def test_file_urls_keep_the_worker(books):
    await sync_to_async(Attachment.objects.create)(file="attachments/a.txt")
    attachments = [attachment async for attachment in Attachment.objects.all()]
    await assert_represented(Files, attachments, 1, many=True)


class Traced(drf.CharField):
    def get_attribute(self, instance):
        return super().get_attribute(instance)


class TracedAuthor(serializers.ModelSerializer):
    name = Traced()

    class Meta:
        model = Author
        fields = ["id", "name"]


@pytest.mark.aiodrf_settings(
    REPRESENTATION_MODE="thread", SERIALIZER_BACKEND_FALLBACK="drf"
)
async def test_a_fields_own_attribute_access_keeps_the_worker(books):
    authors = [author async for author in Author.objects.all()]
    await assert_represented(TracedAuthor, authors, 1, many=True)


@pytest.mark.aiodrf_settings(REPRESENTATION_MODE="thread")
async def test_long_representations_are_not_checked(books):
    from aiodrf.aio._loaded import MAX_CHECKED_OBJECTS

    await sync_to_async(Author.objects.bulk_create)(
        Author(name=f"a{index}") for index in range(MAX_CHECKED_OBJECTS)
    )
    authors = [author async for author in Author.objects.order_by("id")]
    assert len(authors) > MAX_CHECKED_OBJECTS
    # Checking would cost more than the thread hop.
    await assert_represented(AuthorSerializer, authors, 1, many=True)
    await assert_represented(
        AuthorSerializer, authors[:MAX_CHECKED_OBJECTS], 0, many=True
    )


# -- Code of the project's that Django's descriptors would not run ----------------


@isolate_apps("tests.testapp")
def test_a_related_manager_of_the_projects_is_not_trusted():
    from django.db import models

    class Filtering(models.Manager):
        def all(self):
            return super().all().filter(pages__gt=0)  # a new query

    class Writer(models.Model):
        name = models.CharField(max_length=10)

        class Meta:
            app_label = "testapp"

        def __str__(self):
            return self.name

    class Work(models.Model):
        title = models.CharField(max_length=10)
        pages = models.IntegerField(default=1)
        writer = models.ForeignKey(
            Writer, related_name="works", on_delete=models.CASCADE
        )
        objects = Filtering()

        class Meta:
            app_label = "testapp"

        def __str__(self):
            return self.title

    class Works(serializers.ModelSerializer):
        class Meta:
            model = Work
            fields = ["id", "title"]

    class Writers(serializers.ModelSerializer):
        works = Works(many=True)

        class Meta:
            model = Writer
            fields = ["id", "name", "works"]

    writer = Writer(pk=1, name="w")
    prefetched = models.QuerySet(Work)
    prefetched._result_cache = [Work(pk=1, title="t", writer=writer)]
    prefetched._prefetch_done = True
    writer._prefetched_objects_cache = {"works": prefetched}
    assert not reads_loaded(Writers(writer), writer)


@isolate_apps("tests.testapp")
def test_a_property_over_a_column_is_not_trusted():
    from django.db import models

    class Person(models.Model):
        name = models.CharField(max_length=10)

        class Meta:
            app_label = "testapp"

        def __str__(self):
            return self.name

    class Shouting(Person):
        class Meta:
            proxy = True
            app_label = "testapp"

        def __str__(self):
            return self.name.title()

        @property
        def name(self):
            return self.__dict__["name"].upper()

        @name.setter
        def name(self, value):
            self.__dict__["name"] = value

    class People(serializers.ModelSerializer):
        class Meta:
            model = Shouting
            fields = ["id", "name"]

    class Plain(serializers.ModelSerializer):
        class Meta:
            model = Person
            fields = ["id", "name"]

    assert reads_loaded(Plain(Person(pk=1, name="a")), Person(pk=1, name="a"))
    shouting = Shouting(pk=1, name="a")
    assert not reads_loaded(People(shouting), shouting)
