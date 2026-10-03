"""
pydantic models and msgspec Structs used directly by views: the static
serializer resolved and checked at URL construction, ``SchemaViewMixin``,
``ALLOWED_SERIALIZER_BACKENDS``.
"""

import msgspec
import pydantic
import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings
from django.urls import path
from django.utils.asyncio import async_unsafe
from rest_framework import serializers
from rest_framework.permissions import AllowAny

from aiodrf import checks, generics, viewsets
from aiodrf.contrib import typed
from aiodrf.contrib.typed import SchemaViewMixin
from aiodrf.response import Response
from aiodrf.test import AsyncAPIClient, AsyncAPIRequestFactory, count_hops
from aiodrf.views import APIView
from tests.base import both_transports
from tests.testapp.models import Author, Book, Tag


class AuthorModel(pydantic.BaseModel):
    name: str = pydantic.Field(max_length=100)


class AuthorStruct(msgspec.Struct):
    name: str


class BookIn(pydantic.BaseModel):
    title: str
    isbn: str = pydantic.Field(max_length=13)
    pages: int = pydantic.Field(default=100, gt=0)
    author_id: int


class BookOut(pydantic.BaseModel):
    id: int
    title: str
    pages: int
    author_id: int


class BookInStruct(msgspec.Struct):
    title: str
    isbn: str
    author_id: int
    pages: int = 100


class BookOutStruct(msgspec.Struct):
    id: int
    title: str
    pages: int
    author_id: int


class AuthorSerializer(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["name"]


class Open:
    authentication_classes = []
    permission_classes = [AllowAny]


class PydanticBooks(Open, SchemaViewMixin, viewsets.ModelViewSet):
    queryset = Book.objects.all()
    input_schema = BookIn
    output_schema = BookOut
    created = []

    def perform_create(self, serializer):
        type(self).created.append(serializer.validated_object)
        super().perform_create(serializer)


class MsgspecBooks(Open, SchemaViewMixin, viewsets.ModelViewSet):
    queryset = Book.objects.all()
    input_schema = BookInStruct
    output_schema = BookOutStruct


class NewBook(Open, SchemaViewMixin, APIView):
    input_schema = BookIn
    output_schema = BookOut

    async def post(self, request):
        body = await self.aget_validated_body()
        assert isinstance(body, BookIn)
        book = await Book.objects.acreate(**body.model_dump())
        return self.schema_response(book, status=201)


class NewBookSync(Open, SchemaViewMixin, APIView):
    input_schema = BookInStruct
    output_schema = BookOutStruct

    def post(self, request):
        body = self.get_validated_body()
        assert isinstance(body, BookInStruct)
        book = Book.objects.create(**msgspec.structs.asdict(body))
        return self.schema_response([book], many=True, status=201)


def routes(name, viewset):
    return [
        path(f"{name}/", viewset.as_view({"get": "list", "post": "create"})),
        path(
            f"{name}/<int:pk>/",
            viewset.as_view({"get": "retrieve", "patch": "partial_update"}),
        ),
    ]


urlpatterns = [
    *routes("pydantic", PydanticBooks),
    *routes("msgspec", MsgspecBooks),
    path("new/", NewBook.as_view()),
    path("new-sync/", NewBookSync.as_view()),
]
urls = override_settings(ROOT_URLCONF=__name__)


class Fixtures:
    @classmethod
    def setUpTestData(cls):
        cls.author = Author.objects.create(name="Ursula")

    def book(self, isbn="1"):
        return {
            "title": "Earthsea",
            "isbn": isbn,
            "pages": 200,
            "author_id": self.author.pk,
        }


@both_transports
class _SchemaViewSetTests(Fixtures):
    @urls
    async def test_create_list_retrieve_and_patch(self):
        for name in ("pydantic", "msgspec"):
            with self.subTest(name=name):
                created = await self.api(
                    "post", f"/{name}/", data=self.book(name), format="json"
                )
                assert created.status_code == 201, created.data
                book = await Book.objects.aget(isbn=name)
                expected = {"id": book.pk, "title": "Earthsea", "pages": 200}
                assert created.json() == {**expected, "author_id": self.author.pk}
                listed = await self.api("get", f"/{name}/")
                assert {item["id"] for item in listed.json()} >= {book.pk}
                patched = await self.api(
                    "patch", f"/{name}/{book.pk}/", data={"pages": 300}, format="json"
                )
                assert patched.status_code == 200, patched.data
                assert patched.json()["pages"] == 300

    @urls
    async def test_invalid_input_is_drfs_400(self):
        response = await self.api(
            "post", "/pydantic/", data={**self.book(), "pages": 0}, format="json"
        )
        assert response.status_code == 400
        assert list(response.json()) == ["pages"]

    @urls
    async def test_the_validated_object_reaches_perform_create(self):
        PydanticBooks.created.clear()
        await self.api("post", "/pydantic/", data=self.book("obj"), format="json")
        assert PydanticBooks.created == [BookIn(**self.book("obj"))]


@both_transports
class _SchemaAPIViewTests(Fixtures):
    @urls
    async def test_the_body_is_the_input_schema_and_the_response_the_output(self):
        response = await self.api("post", "/new/", data=self.book("a"), format="json")
        assert response.status_code == 201, response.content
        book = await Book.objects.aget(isbn="a")
        assert response.json() == {
            "id": book.pk,
            "title": "Earthsea",
            "pages": 200,
            "author_id": self.author.pk,
        }

    @urls
    async def test_sync_handlers_have_the_same_helpers(self):
        response = await self.api(
            "post", "/new-sync/", data=self.book("s"), format="json"
        )
        assert response.status_code == 201, response.content
        assert [item["title"] for item in response.json()] == ["Earthsea"]

    @urls
    async def test_invalid_bodies_are_drfs_400(self):
        response = await self.api("post", "/new/", data={"title": "x"}, format="json")
        assert response.status_code == 400
        assert set(response.json()) == {"isbn", "author_id"}


class HopTests(Fixtures, TestCase):
    @urls
    async def test_a_schema_create_costs_what_a_serializer_create_costs(self):
        with count_hops() as hops:
            response = await AsyncAPIClient().post(
                "/pydantic/", self.book("h"), format="json"
            )
        assert response.status_code == 201, response.data
        assert hops.calls == ["CreateModelMixin._create"]


# -- Resolution at URL construction -------------------------------------------


def test_a_bare_schema_is_adapted_when_the_url_is_built():
    class Schema(pydantic.BaseModel):
        name: str

    class View(Open, generics.ListCreateAPIView):
        queryset = Author.objects.all()
        serializer_class = Schema

    assert Schema not in typed._adapted
    View.as_view()
    assert Schema in typed._adapted


def test_the_schema_pair_is_built_when_the_url_is_built():
    class In(msgspec.Struct):
        name: str

    class Out(msgspec.Struct):
        id: int
        name: str

    class Pair(Open, SchemaViewMixin, generics.CreateAPIView):
        queryset = Author.objects.all()
        input_schema = In
        output_schema = Out

    assert (In, Out, Author) not in typed._adapted
    Pair.as_view()
    assert (In, Out, Author) in typed._adapted


def test_input_and_output_schemas_must_come_from_one_library():
    class Mixed(Open, SchemaViewMixin, generics.CreateAPIView):
        queryset = Book.objects.all()
        input_schema = BookIn
        output_schema = BookOutStruct

    with pytest.raises(ImproperlyConfigured, match="one library"):
        Mixed.as_view()


def test_a_schema_view_needs_a_schema():
    class Empty(Open, SchemaViewMixin, APIView):
        pass

    with pytest.raises(ImproperlyConfigured, match="input_schema"):
        Empty.as_view()


# -- ALLOWED_SERIALIZER_BACKENDS ----------------------------------------------


@pytest.mark.parametrize(
    ("allowed", "serializer_class", "backend"),
    [
        (["pydantic"], AuthorSerializer, "drf"),
        (["drf"], AuthorModel, "pydantic"),
        (["drf", "pydantic"], AuthorStruct, "msgspec"),
    ],
)
def test_a_serializer_kind_that_is_not_allowed_fails_at_url_construction(
    allowed, serializer_class, backend
):
    class View(Open, generics.ListCreateAPIView):
        queryset = Author.objects.all()

    with (
        override_settings(FASTDRF={"ALLOWED_SERIALIZER_BACKENDS": allowed}, AIODRF={}),
        pytest.raises(ImproperlyConfigured, match=rf"a {backend} serializer"),
    ):
        View.as_view(serializer_class=serializer_class)


def test_schema_views_and_viewsets_are_checked_too():
    with override_settings(
        FASTDRF={"ALLOWED_SERIALIZER_BACKENDS": ["drf", "msgspec"]}, AIODRF={}
    ):
        with pytest.raises(ImproperlyConfigured, match="a pydantic serializer"):
            PydanticBooks.as_view({"get": "list"})
        MsgspecBooks.as_view({"get": "list"})


@pytest.mark.django_db(transaction=True)
async def test_a_serializer_chosen_per_request_is_checked_when_it_is_built():
    class Dynamic(Open, generics.ListAPIView):
        queryset = Author.objects.all()

        def get_serializer_class(self):
            return AuthorModel  # a bare model: adapted here as well

    view = Dynamic.as_view()
    await Author.objects.acreate(name="Ursula")
    from aiodrf.test import AsyncAPIRequestFactory

    response = await view(AsyncAPIRequestFactory().get("/"))
    assert response.data == [{"name": "Ursula"}]
    with (
        override_settings(FASTDRF={"ALLOWED_SERIALIZER_BACKENDS": ["drf"]}, AIODRF={}),
        pytest.raises(ImproperlyConfigured, match="a pydantic serializer"),
    ):
        await view(AsyncAPIRequestFactory().get("/"))


@pytest.mark.parametrize("value", [[], ["drf", "marshmallow"], "drf"])
def test_the_setting_is_validated(value):
    from fastdrf.checks import check_settings

    with override_settings(FASTDRF={"ALLOWED_SERIALIZER_BACKENDS": value}, AIODRF={}):
        ids = [message.id for message in check_settings(app_configs=None)]
    assert ids == ["fastdrf.E001"]


def test_the_system_check_reports_every_view_that_is_not_allowed():
    with override_settings(
        ROOT_URLCONF=__name__,
        FASTDRF={"ALLOWED_SERIALIZER_BACKENDS": ["drf"]},
        AIODRF={},
    ):
        messages = checks.check_serializer_backends(app_configs=None)
    assert {message.id for message in messages} == {"aiodrf.E005"}
    named = " ".join(message.msg for message in messages)
    for view in ("PydanticBooks", "MsgspecBooks", "NewBook", "NewBookSync"):
        assert view in named


# -- What else sees the schemas -------------------------------------------------


def test_openapi_documents_the_input_and_output_schemas():
    from drf_spectacular.generators import SchemaGenerator

    with override_settings(ROOT_URLCONF=__name__):
        schema = SchemaGenerator().get_schema(request=None, public=True)
    # An APIView's POST is documented as 200, as spectacular does for any APIView.
    for path_name, status in (("/new/", "200"), ("/pydantic/", "201")):
        operation = schema["paths"][path_name]["post"]
        body = operation["requestBody"]["content"]["application/json"]["schema"]
        response = operation["responses"][status]["content"]["application/json"][
            "schema"
        ]
        assert body == {"$ref": "#/components/schemas/BookIn"}
        assert response == {"$ref": "#/components/schemas/BookOut"}


def test_the_validated_object_needs_valid_input():
    serializer_class = typed.schema_serializer(BookIn, BookOut)
    serializer = serializer_class(data={"title": "x"})
    with pytest.raises(AssertionError, match="is_valid"):
        serializer.validated_object  # noqa: B018
    assert not serializer.is_valid()
    with pytest.raises(AssertionError, match="valid input"):
        serializer.validated_object  # noqa: B018


def test_a_partial_body_is_an_instance_of_the_partial_schema():
    serializer = typed.schema_serializer(BookInStruct, BookOutStruct)(
        data={"pages": 5}, partial=True
    )
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == {"pages": 5}
    assert serializer.validated_object.pages == 5
    assert serializer.validated_object.title is msgspec.UNSET


class TaggedBookIn(pydantic.BaseModel):
    title: str
    isbn: str
    author_id: int
    tags: list[int] = []


class TaggedBookInStruct(msgspec.Struct):
    title: str
    isbn: str
    author_id: int
    tags: list[int] = []


@pytest.mark.django_db
@pytest.mark.parametrize(
    ("schema", "output"), [(TaggedBookIn, BookOut), (TaggedBookInStruct, BookOutStruct)]
)
def test_a_many_to_many_field_is_set_after_a_save_as_by_drf(schema, output):
    author = Author.objects.create(name="Ada")
    red, blue = Tag.objects.create(name="red"), Tag.objects.create(name="blue")
    serializer_class = typed.schema_serializer(schema, output, model=Book)
    data = {"title": "t", "isbn": "1", "author_id": author.pk, "tags": [red.pk]}
    serializer = serializer_class(data=data)
    assert serializer.is_valid(), serializer.errors
    book = serializer.save()
    assert list(book.tags.all()) == [red]
    serializer = serializer_class(book, data={**data, "tags": [blue.pk]})
    assert serializer.is_valid(), serializer.errors
    assert list(serializer.save().tags.all()) == [blue]


# -- The view's configuration, startup checks and worker boundary --------------------


class Name(pydantic.BaseModel):
    name: str


class Names(Open, SchemaViewMixin, generics.CreateAPIView):
    queryset = Author.objects.all()
    input_schema = Name


def test_the_model_follows_the_queryset_given_to_as_view():
    for queryset, model in ((None, Author), (Tag.objects.all(), Tag)):
        callback = Names.as_view(**({} if queryset is None else {"queryset": queryset}))
        view = callback.view_class(**callback.view_initkwargs)
        assert view.get_queryset().model is model
        assert view.get_serializer_class().Meta.model is model
    # The class keeps its own model after a view was built for another one.
    assert Names().get_serializer_class().Meta.model is Author


class TagNames(TestCase):
    async def test_a_create_writes_the_model_given_to_as_view(self):
        view = Names.as_view(queryset=Tag.objects.all())
        response = await view(
            AsyncAPIRequestFactory().post("/", {"name": "new"}, format="json")
        )
        assert response.status_code == 201, response.data
        assert await Tag.objects.filter(name="new").aexists()
        assert not await Author.objects.filter(name="new").aexists()


def test_the_views_startup_checks_all_run():
    class Checked(generics.CreateAPIView):
        @classmethod
        def _compile_serializers(cls, initkwargs):
            super()._compile_serializers(initkwargs)
            raise ImproperlyConfigured("a later mixin's check")

    class View(SchemaViewMixin, Checked):
        input_schema = Name

    with pytest.raises(ImproperlyConfigured, match="a later mixin's check"):
        View.as_view()


class ContextBody(Open, SchemaViewMixin, APIView):
    input_schema = Name

    @async_unsafe("get_serializer_context ran on the event loop")
    def get_serializer_context(self):
        return {"request": self.request, "view": self}

    async def post(self, request):
        return Response({"name": (await self.aget_validated_body()).name})


class BodyTests(TestCase):
    async def test_a_synchronous_context_is_built_in_the_worker(self):
        view = ContextBody.as_view()
        response = await view(
            AsyncAPIRequestFactory().post("/", {"name": "n"}, format="json")
        )
        assert response.status_code == 200, response.data
        assert response.data == {"name": "n"}


def test_a_pydantic_v1_model_is_refused_at_url_construction():
    # The pydantic adapter is for pydantic 2's ``BaseModel``.
    import pydantic.v1

    class V1Author(pydantic.v1.BaseModel):
        name: str

    class View(Open, generics.ListCreateAPIView):
        queryset = Author.objects.all()
        serializer_class = V1Author

    with pytest.raises(ImproperlyConfigured, match=r"pydantic\.v1"):
        View.as_view()


def test_aiodrfs_view_classes_leave_the_description_to_the_project():
    # drf-spectacular describes an operation with the first docstring in the
    # view's MRO before DRF's classes: one of aiodrf's would be published.
    import inspect

    from drf_spectacular.plumbing import get_doc

    from aiodrf import generics as aio_generics
    from aiodrf import mixins as aio_mixins
    from aiodrf import views as aio_views
    from aiodrf import viewsets as aio_viewsets

    classes = [
        aio_views.APIView,
        SchemaViewMixin,
        *(
            value
            for module in (aio_generics, aio_mixins, aio_viewsets)
            for value in vars(module).values()
            if isinstance(value, type)
            and value.__module__ == module.__name__
            and (
                issubclass(value, aio_views.APIView) or value.__name__.endswith("Mixin")
            )
        ),
    ]
    from rest_framework.generics import GenericAPIView

    def view(cls):
        # A mixin is used with a DRF view.
        bases = (cls,) if issubclass(cls, aio_views.APIView) else (cls, GenericAPIView)
        return type("V", bases, {})

    def published_by_aiodrf(cls):
        # DRF's own docstrings are drf-spectacular's to exclude, not aiodrf's.
        doc = get_doc(view(cls))
        return doc and any(
            klass.__module__.startswith("aiodrf.")
            and klass.__doc__
            and inspect.cleandoc(klass.__doc__) == doc
            for klass in view(cls).__mro__
        )

    leaking = [cls.__qualname__ for cls in classes if published_by_aiodrf(cls)]
    assert leaking == []
