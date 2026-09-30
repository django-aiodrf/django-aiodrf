"""
``aiodrf.contrib.async_backend`` through Django's ASGI and WSGI handlers, on
PostgreSQL with django-async-backend's engine (``nox -s native_db``).

Test data is written with Django's ORM: a native connection belongs to the
first task that used it, and the test's task is not the request's.
"""

import asyncio
import io
import threading
from unittest import mock

import pytest
from asgiref.sync import SyncToAsync, async_to_sync, sync_to_async
from django.conf import settings
from django.contrib.auth.models import Permission, User
from django.core.exceptions import ImproperlyConfigured
from django.core.handlers.asgi import ASGIHandler
from django.core.handlers.wsgi import WSGIHandler
from django.core.paginator import Paginator
from django.db import connection
from django.db.models.signals import m2m_changed, post_save
from django.test import override_settings
from django.utils.asyncio import async_unsafe
from django.utils.functional import cached_property
from django_async_backend.db import async_connections, async_new_connection
from rest_framework import pagination as drf_pagination
from rest_framework import serializers as drf_serializers
from rest_framework.exceptions import PermissionDenied

from aiodrf.contrib import async_backend as native
from aiodrf.test import APIClient, AsyncAPIClient, AsyncAPIRequestFactory, count_hops
from tests.async_backend.models import Label, Note, Person
from tests.async_backend.urls import Authors, AuthorSerializer, Books
from tests.testapp.models import Author, Book, Tag


def orm(func, *args, **kwargs):
    return sync_to_async(func)(*args, **kwargs)


async def library():
    ursula = await orm(Author.objects.create, name="Ursula")
    stanislaw = await orm(Author.objects.create, name="Stanisław")
    fantasy = await orm(Tag.objects.create, name="fantasy")
    scifi = await orm(Tag.objects.create, name="scifi")
    books = []
    for index, (title, author, pages) in enumerate(
        [
            ("Earthsea", ursula, 200),
            ("Solaris", stanislaw, 300),
            ("The Dispossessed", ursula, 350),
            ("Eden", stanislaw, 250),
        ]
    ):
        book = await orm(
            Book.objects.create,
            title=title,
            isbn=str(index),
            author=author,
            pages=pages,
        )
        await orm(book.tags.set, [fantasy] if author is ursula else [scifi])
        books.append(book)
    return {
        "ursula": ursula,
        "stanislaw": stanislaw,
        "fantasy": fantasy,
        "scifi": scifi,
    }, books


# -- Reading -------------------------------------------------------------------------


async def test_pages_with_filtering_search_and_ordering(api):
    lib, _ = await library()
    first = await api("get", "/books/")
    assert first.status_code == 200, first.content
    body = first.json()
    assert body["count"] == 4
    assert body["next"].endswith("/books/?page=2")
    assert body["previous"] is None
    assert [b["title"] for b in body["results"]] == ["Earthsea", "Solaris"]
    assert body["results"][0]["author_name"] == "Ursula"
    assert body["results"][0]["tags"] == [lib["fantasy"].pk]

    filtered = await api("get", f"/books/?author={lib['stanislaw'].pk}&ordering=-pages")
    assert [b["title"] for b in filtered.json()["results"]] == ["Solaris", "Eden"]
    searched = await api("get", "/books/?search=the")
    assert [b["title"] for b in searched.json()["results"]] == ["The Dispossessed"]
    beyond = await api("get", "/books/?page=9")
    assert beyond.status_code == 404
    assert "Invalid page" in beyond.json()["detail"]


async def test_limit_offset_and_cursor_pages(api):
    await library()
    limited = await api("get", "/limited/?limit=3&offset=1")
    assert limited.json()["count"] == 4
    assert [b["title"] for b in limited.json()["results"]] == [
        "Solaris",
        "The Dispossessed",
        "Eden",
    ]

    titles, url = [], "/cursor/"
    while url:
        page = (await api("get", url)).json()
        titles += [b["title"] for b in page["results"]]
        url = page["next"] and page["next"].replace("http://testserver", "")
    assert titles == ["Earthsea", "Solaris", "The Dispossessed", "Eden"]


async def test_an_unpaginated_list(api):
    lib, _ = await library()
    authors = await api("get", "/authors/")
    assert authors.json() == [
        {"id": lib["ursula"].pk, "name": "Ursula"},
        {"id": lib["stanislaw"].pk, "name": "Stanisław"},
    ]


async def test_the_class_queryset_is_read_afresh_on_each_request(api):
    # DRF's get_queryset() clones only Django querysets; a native one kept
    # on the class would cache the first request's rows.
    await orm(Author.objects.create, name="Ursula")
    assert len((await api("get", "/authors/")).json()) == 1
    await orm(Author.objects.create, name="Stanisław")
    assert len((await api("get", "/authors/")).json()) == 2


async def test_retrieve_404_and_object_permissions(api):
    _, books = await library()
    got = await api("get", f"/books/{books[0].pk}/")
    assert got.status_code == 200
    assert got.json()["title"] == "Earthsea"
    assert (await api("get", "/books/999999/")).status_code == 404
    assert (await api("get", "/books/not-a-number/")).status_code == 404
    await orm(Book.objects.filter(pk=books[1].pk).update, title="private")
    assert (await api("get", f"/books/{books[1].pk}/")).status_code == 403


@pytest.mark.parametrize("client_class", [APIClient, AsyncAPIClient])
def test_requests_leave_no_session_open(client_class):
    # Without a running loop, as under a WSGI server: each request runs in
    # an event loop of its own, which its connection must not outlive.
    Author.objects.create(name="Ursula")
    client = client_class()
    get = client.get if client_class is APIClient else async_to_sync(client.get)
    for _ in range(3):
        assert get("/authors/").status_code == 200
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*) FROM pg_stat_activity"
            " WHERE datname = current_database() AND pid <> pg_backend_pid()"
        )
        assert cursor.fetchone() == (0,)


async def test_a_synchronous_check_object_permissions_override_is_called():
    _, books = await library()

    class Checked(Books):
        def check_object_permissions(self, request, obj):
            raise PermissionDenied("checked")

    view = async_new_connection(Checked.as_view({"get": "retrieve"}))
    response = await view(AsyncAPIRequestFactory().get("/"), pk=books[0].pk)
    assert response.status_code == 403
    assert response.data["detail"] == "checked"


@pytest.mark.parametrize("kind", ["sync", "async"])
async def test_a_get_queryset_override_scopes_list_and_retrieve(kind):
    lib, books = await library()

    class Scoped(Books):
        pagination_class = None

    if kind == "sync":
        Scoped.get_queryset = lambda self: Book.async_objects.filter(
            author=lib["ursula"]
        )
    else:

        async def aget_queryset(self):
            return Book.async_objects.filter(author=lib["ursula"])

        Scoped.aget_queryset = aget_queryset

    listing = async_new_connection(Scoped.as_view({"get": "list"}))
    with count_hops() as hops:
        response = await listing(AsyncAPIRequestFactory().get("/"))
    assert [b["title"] for b in response.data] == ["Earthsea", "The Dispossessed"]
    # The filter backends and the representation; a synchronous override
    # one more, before the read.
    assert hops.count == (3 if kind == "sync" else 2), hops.calls
    detail = async_new_connection(Scoped.as_view({"get": "retrieve"}))
    response = await detail(AsyncAPIRequestFactory().get("/"), pk=books[1].pk)
    assert response.status_code == 404


@pytest.mark.parametrize("action", ["list", "retrieve"])
async def test_an_optimize_queryset_override_without_super(action):
    # ``optimize_queryset`` is a documented hook; the view's own
    # ``prefetch_related`` still applies without ``super()``.
    _, books = await library()

    class Optimized(Books):
        pagination_class = None
        prefetch_related = ("tags",)

        def optimize_queryset(self, queryset):
            return queryset.select_related("author")

    view = async_new_connection(Optimized.as_view({"get": action}))
    kwargs = {"pk": books[0].pk} if action == "retrieve" else {}
    response = await view(AsyncAPIRequestFactory().get("/"), **kwargs)
    assert response.status_code == 200, response.data


class OnTheLoop:
    """Hooks written for DRF, which may query: they must not run on the event loop."""

    authentication_classes = []
    permission_classes = []


def off_the_loop(func):
    return async_unsafe(f"{func.__qualname__} ran on the event loop")(func)


class ContextView(OnTheLoop, native.ListAPIView):
    queryset = Author.async_objects.order_by("id")
    serializer_class = Authors.serializer_class
    pagination_class = None

    @off_the_loop
    def get_serializer_context(self):
        return super().get_serializer_context()


class SerializerClassView(ContextView):
    get_serializer_context = native.ListAPIView.get_serializer_context

    @off_the_loop
    def get_serializer_class(self):
        return super().get_serializer_class()


class FilterView(ContextView):
    get_serializer_context = native.ListAPIView.get_serializer_context
    filter_backends = []

    @off_the_loop
    def filter_queryset(self, queryset):
        return queryset.filter(name__startswith="U")


class PrefetchedBooks(OnTheLoop, native.RetrieveAPIView):
    queryset = Book.async_objects.all()

    class serializer_class(Books.serializer_class):
        class Meta(Books.serializer_class.Meta):
            pass

        @off_the_loop
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)


class SizedPages(native.pagination.PageNumberPagination):
    @off_the_loop
    def get_page_size(self, request):
        return 1


class PagedView(ContextView):
    get_serializer_context = native.ListAPIView.get_serializer_context
    pagination_class = SizedPages


@pytest.mark.parametrize(
    ("view", "kwargs"),
    [
        (ContextView, {}),
        (SerializerClassView, {}),
        (FilterView, {}),
        (PagedView, {}),
        (PrefetchedBooks, {"pk": "first"}),
    ],
)
async def test_synchronous_hooks_run_in_a_worker(view, kwargs):
    _, books = await library()
    if kwargs:
        kwargs = {"pk": books[0].pk}
    response = await async_new_connection(view.as_view())(
        AsyncAPIRequestFactory().get("/"), **kwargs
    )
    assert response.status_code == 200, response.data


class CappedLimits(native.pagination.LimitOffsetPagination):
    # A capped or estimated count, as projects use for large tables.
    @off_the_loop
    def get_count(self, queryset):
        return 3


class EstimatedLimits(native.pagination.LimitOffsetPagination):
    async def get_count(self, queryset):
        return 3


class CappedPaginator(Paginator):
    @cached_property
    @off_the_loop
    def count(self):
        return 3


class CappedPages(native.pagination.PageNumberPagination):
    page_size = 2
    django_paginator_class = CappedPaginator


@pytest.mark.parametrize("paginator", [CappedLimits, EstimatedLimits, CappedPages])
async def test_the_paginators_count_is_the_projects(paginator):
    # DRF's LimitOffsetPagination counts with get_count(), and its
    # PageNumberPagination with the Django paginator's count.
    await library()

    class Counted(Books):
        pagination_class = paginator

    view = async_new_connection(Counted.as_view({"get": "list"}))
    response = await view(AsyncAPIRequestFactory().get("/?limit=2"))
    assert response.status_code == 200, response.data
    assert response.data["count"] == 3


# -- Writing -------------------------------------------------------------------------


@pytest.fixture
def signals():
    received = []

    def on_save(sender, instance, created, **kwargs):
        received.append(("post_save", sender.__name__, created))

    def on_m2m(sender, action, pk_set, **kwargs):
        received.append(("m2m_changed", action, sorted(pk_set or ())))

    post_save.connect(on_save, sender=Book)
    m2m_changed.connect(on_m2m, sender=Book.tags.through)
    yield received
    post_save.disconnect(on_save, sender=Book)
    m2m_changed.disconnect(on_m2m, sender=Book.tags.through)


async def test_a_created_row_is_represented_in_the_worker_in_inline_mode():
    # Inline mode vouches for the project's querysets; the to-many relations
    # of a row the view just saved are not loaded.
    lib, _ = await library()
    with override_settings(AIODRF={"REPRESENTATION_MODE": "inline"}):
        created = await AsyncAPIClient().post(
            "/books/",
            {"title": "T", "isbn": "T", "author": lib["ursula"].pk, "tags": []},
            format="json",
        )
    assert created.status_code == 201, created.content
    assert created.json()["tags"] == []


async def test_create_with_many_to_many(api, signals):
    lib, _ = await library()
    signals.clear()
    created = await api(
        "post",
        "/books/",
        {
            "title": "Lathe",
            "isbn": "L",
            "author": lib["ursula"].pk,
            "tags": [lib["fantasy"].pk, lib["scifi"].pk],
        },
    )
    assert created.status_code == 201, created.content
    assert sorted(created.json()["tags"]) == sorted(
        [lib["fantasy"].pk, lib["scifi"].pk]
    )
    book = await orm(Book.objects.get, isbn="L")
    assert sorted(
        await orm(lambda: list(book.tags.values_list("pk", flat=True)))
    ) == sorted([lib["fantasy"].pk, lib["scifi"].pk])
    assert signals == [
        ("post_save", "Book", True),
        ("m2m_changed", "pre_add", sorted([lib["fantasy"].pk, lib["scifi"].pk])),
        ("m2m_changed", "post_add", sorted([lib["fantasy"].pk, lib["scifi"].pk])),
    ]


async def test_update_replaces_the_many_to_many_set(api, signals):
    lib, books = await library()
    signals.clear()
    updated = await api(
        "patch",
        f"/books/{books[0].pk}/",
        {"title": "A Wizard", "tags": [lib["scifi"].pk]},
    )
    assert updated.status_code == 200, updated.content
    assert updated.json()["tags"] == [lib["scifi"].pk]
    assert await orm(
        lambda: list(Book.objects.get(pk=books[0].pk).tags.values_list("pk", flat=True))
    ) == [lib["scifi"].pk]
    assert signals == [
        ("post_save", "Book", False),
        ("m2m_changed", "pre_remove", [lib["fantasy"].pk]),
        ("m2m_changed", "post_remove", [lib["fantasy"].pk]),
        ("m2m_changed", "pre_add", [lib["scifi"].pk]),
        ("m2m_changed", "post_add", [lib["scifi"].pk]),
    ]
    put = await api(
        "put",
        f"/books/{books[0].pk}/",
        {
            "title": "Earthsea",
            "isbn": "0",
            "pages": 1,
            "author": lib["ursula"].pk,
            "tags": [],
        },
    )
    assert put.status_code == 200
    assert put.json()["tags"] == []


async def test_validation_errors_are_drfs(api):
    lib, _ = await library()
    duplicate = await api(
        "post", "/books/", {"title": "x", "isbn": "0", "author": lib["ursula"].pk}
    )
    assert duplicate.status_code == 400
    assert duplicate.json() == {"isbn": ["book with this isbn already exists."]}


async def test_a_failure_in_the_save_rolls_back_with_atomic_save(api):
    lib, _ = await library()

    def refuse(sender, action, **kwargs):
        if action == "pre_add":
            raise RuntimeError("refused")

    m2m_changed.connect(refuse, sender=Book.tags.through)
    try:
        with pytest.raises(RuntimeError, match="refused"):
            await api(
                "post",
                "/books/",
                {
                    "title": "Gone",
                    "isbn": "G",
                    "author": lib["ursula"].pk,
                    "tags": [lib["fantasy"].pk],
                },
            )
        assert not await orm(Book.objects.filter(isbn="G").exists)
        with (
            override_settings(AIODRF={"ATOMIC_SAVE": False}),
            pytest.raises(RuntimeError, match="refused"),
        ):
            await api(
                "post",
                "/books/",
                {
                    "title": "Kept",
                    "isbn": "K",
                    "author": lib["ursula"].pk,
                    "tags": [lib["fantasy"].pk],
                },
            )
        assert await orm(Book.objects.filter(isbn="K").exists)
    finally:
        m2m_changed.disconnect(refuse, sender=Book.tags.through)


async def test_destroy_cascades_natively(api):
    lib, _ = await library()
    deleted = await api("delete", f"/authors/{lib['ursula'].pk}/")
    assert deleted.status_code == 204
    assert await orm(Book.objects.filter(author_id=lib["ursula"].pk).count) == 0
    assert await orm(Book.objects.count) == 2


async def test_symmetrical_many_to_many(api):
    ada = await orm(Person.objects.create, name="Ada")
    bo = await orm(Person.objects.create, name="Bo")
    cy = await orm(Person.objects.create, name="Cy")
    await api("patch", f"/people/{ada.pk}/", {"friends": [bo.pk, cy.pk]})
    assert sorted(await orm(lambda: [p.name for p in bo.friends.all()])) == ["Ada"]
    await api("patch", f"/people/{ada.pk}/", {"friends": [cy.pk]})
    assert await orm(lambda: [p.name for p in bo.friends.all()]) == []
    assert await orm(lambda: [p.name for p in cy.friends.all()]) == ["Ada"]


@pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")
async def test_setting_many_to_many_leaves_rows_the_default_manager_hides(api):
    shown = await orm(Label.objects.create, name="shown")
    hidden = await orm(Label.objects.create, name="hidden", hidden=True)
    new = await orm(Label.objects.create, name="new")
    native_note = await orm(Note.objects.create, text="native")
    django_note = await orm(Note.objects.create, text="django")
    for note in (native_note, django_note):
        await orm(
            Note.labels.through.objects.bulk_create,
            [
                Note.labels.through(note=note, label=shown),
                Note.labels.through(note=note, label=hidden),
            ],
        )

    response = await api("patch", f"/notes/{native_note.pk}/", {"labels": [new.pk]})
    assert response.status_code == 200, response.content
    await orm(django_note.labels.set, [new])

    def label_ids(note):
        rows = Note.labels.through.objects.filter(note=note).order_by("label_id")
        return list(rows.values_list("label_id", flat=True))

    assert await orm(label_ids, native_note) == await orm(label_ids, django_note)
    assert await orm(label_ids, native_note) == [hidden.pk, new.pk]


@pytest.mark.django_db(transaction=True, databases=["default", "other"])
async def test_a_failed_update_rolls_back_on_the_instances_database():
    # async_save() writes where the instance was read from; the transaction
    # must be opened there too.
    class Titles(native.ModelSerializer):
        class Meta:
            model = Book
            fields = ["title", "tags"]

    async def fail(*args):
        raise ValueError("M2M failure")

    try:
        author = await Author.async_objects.using("other").acreate(name="Ursula")
        book = await Book.async_objects.using("other").acreate(
            title="before", isbn="other-1", author_id=author.pk
        )
        with (
            mock.patch("aiodrf.contrib.async_backend.serializers.aset_many", fail),
            pytest.raises(ValueError, match="M2M failure"),
        ):
            await Titles().aupdate(book, {"title": "after", "tags": []})
        assert (
            await Book.async_objects.using("other").aget(pk=book.pk)
        ).title == "before"
    finally:
        for connection in async_connections.all():
            await connection.close()


# -- Synchronous callers, concurrency, streaming -----------------------------------------


async def test_synchronous_callers_get_a_connection_of_their_own():
    _, books = await library()

    def sync_caller():
        view = Books(action_map={"get": "retrieve", "patch": "partial_update"})
        view.setup(AsyncAPIRequestFactory().get("/"), pk=books[0].pk)
        view.request = view.initialize_request(view.request)
        view.format_kwarg = None
        obj = view.get_object()  # the browsable API's path
        serializer = view.get_serializer(obj, data={"title": "Sync"}, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return obj.title

    assert await orm(sync_caller) == "Sync"
    assert (await orm(Book.objects.get, pk=books[0].pk)).title == "Sync"


async def test_concurrent_requests_each_use_their_own_connection():
    lib, _ = await library()

    async def write(index):
        return await AsyncAPIClient().post(
            "/books/",
            {"title": f"c{index}", "isbn": f"c{index}", "author": lib["ursula"].pk},
            format="json",
        )

    async def read():
        return await AsyncAPIClient().get("/books/")

    responses = await asyncio.gather(
        *[write(i) for i in range(6)], *[read() for _ in range(6)]
    )
    assert [r.status_code for r in responses] == [201] * 6 + [200] * 6
    assert await orm(Book.objects.filter(isbn__startswith="c").count) == 6


async def asgi_get(path, query=""):
    """
    Django's ASGI handler, called directly: the test client closes a streaming
    response with a synchronous ``close()`` on the event loop, which cannot
    send ``request_finished`` to django-async-backend's async receiver.
    """
    # The request, then nothing: the client never disconnects.
    inbox = asyncio.Queue()
    inbox.put_nowait({"type": "http.request", "body": b"", "more_body": False})
    messages = []

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "query_string": query.removeprefix("?").encode(),
        "headers": [],
        "server": ("testserver", 80),
    }
    await ASGIHandler()(scope, inbox.get, send)
    status = messages[0]["status"]
    return status, b"".join(m.get("body", b"") for m in messages[1:])


@pytest.mark.parametrize("query", ["", "?keepalive"])
async def test_an_event_stream_reads_natively(query):
    _, books = await library()
    status, body = await asgi_get(f"/events/{books[1].pk}/", query)
    assert status == 200
    titles = [line.removeprefix(b"data: ") for line in body.split(b"\n\n") if line]
    assert titles == [
        b'{"title":"Solaris"}',
        b'{"title":"Earthsea"}',
        b'{"title":"The Dispossessed"}',
        b'{"title":"Eden"}',
    ]


@pytest.mark.parametrize("client_class", [AsyncAPIClient, APIClient])
async def test_django_model_permissions_with_a_native_view(client_class):
    lib, _ = await library()
    viewer = await orm(User.objects.create_user, "viewer")
    editor = await orm(User.objects.create_user, "editor")
    await orm(
        lambda: editor.user_permissions.add(Permission.objects.get(codename="add_book"))
    )
    client = client_class()

    async def call(method, url, data=None):
        if client_class is APIClient:
            return await sync_to_async(getattr(client, method))(
                url, data, format="json"
            )
        return await getattr(client, method)(url, data, format="json")

    def as_user(user):
        client.force_authenticate(user)

    as_user(viewer)
    listed = await call("get", "/permitted/")
    assert listed.status_code == 200
    assert len(listed.json()) == 4
    book = {"title": "New", "isbn": "N", "author": lib["ursula"].pk}
    assert (await call("post", "/permitted/", book)).status_code == 403
    as_user(editor)
    assert (await call("post", "/permitted/", book)).status_code == 201


@pytest.mark.parametrize("client_class", [AsyncAPIClient, APIClient])
async def test_the_browsable_api_renders_native_views(client_class):
    _, books = await library()
    client = client_class()

    def get(url):
        return client.get(url, HTTP_ACCEPT="text/html")

    for url in ("/books/", f"/books/{books[0].pk}/"):
        if client_class is APIClient:
            response = await sync_to_async(get)(url)
        else:
            response = await get(url)
        assert response.status_code == 200
        assert response["Content-Type"].startswith("text/html")
        assert b"Earthsea" in response.content


def wsgi_get(path, query=""):
    """Django's WSGI handler, called as a WSGI server does (see asgi_get)."""
    statuses = []
    environ = {
        "REQUEST_METHOD": "GET",
        "PATH_INFO": path,
        "QUERY_STRING": query.removeprefix("?"),
        "SERVER_NAME": "testserver",
        "SERVER_PORT": "80",
        "wsgi.url_scheme": "http",
        "wsgi.input": io.BytesIO(),
        "wsgi.errors": io.StringIO(),
    }
    result = WSGIHandler()(environ, lambda status, headers: statuses.append(status))
    try:
        body = b"".join(result)
    finally:
        result.close()
    return statuses[0], body


@pytest.mark.parametrize("query", ["", "?keepalive"])
def test_an_event_stream_reads_natively_under_wsgi(query):
    _, books = async_to_sync(library)()
    # Django collects the async source in an event loop of its own, after
    # the view's connection scope closed.
    with pytest.warns(Warning, match="must consume asynchronous iterators"):
        status, body = wsgi_get(f"/events/{books[1].pk}/", query)
    assert status == "200 OK"
    titles = [line.removeprefix(b"data: ") for line in body.split(b"\n\n") if line]
    assert titles == [
        b'{"title":"Solaris"}',
        b'{"title":"Earthsea"}',
        b'{"title":"The Dispossessed"}',
        b'{"title":"Eden"}',
    ]


# -- Costs -------------------------------------------------------------------------------


@pytest.mark.aiodrf_settings(REPRESENTATION_MODE="thread")
async def test_hops():
    _, books = await library()
    client = AsyncAPIClient()
    with count_hops() as hops:
        await client.get("/authors/")
    listing = list(hops.calls)
    with count_hops() as hops:
        await client.get(f"/books/{books[0].pk}/")
    detail = list(hops.calls)
    with count_hops() as hops:
        created = await client.post("/authors/", {"name": "New"}, format="json")
    create = list(hops.calls)
    assert created.status_code == 201
    # Listing: none, the rows it read are represented on the loop. Detail: the
    # three filter backends (one hop), NotOwner, the representation (a dotted
    # source, prefetched tags). Create: validation; the native write, in
    # Django's transaction for the receivers of django-cleanup and
    # django-cacheops, which the suite installs (two hops, see
    # test_on_commit.test_hops); the saved row is represented on the loop.
    assert (len(listing), len(detail), len(create)) == (0, 3, 3), (
        listing,
        detail,
        create,
    )


class Named(native.ModelSerializer):
    shout = drf_serializers.SerializerMethodField()

    class Meta:
        model = Author
        fields = ["id", "shout"]

    def get_shout(self, author):
        return author.name.upper()


class OwnContext(Authors):
    def get_serializer_context(self):
        return {**super().get_serializer_context(), "own": True}


class Shouting(Authors):
    serializer_class = Named


@pytest.mark.parametrize(
    ("view", "hops"),
    [
        pytest.param(Authors, (0, 0), id="read rows represented on the loop"),
        # The context may be the project's code: it and the queryset in one
        # hop, the representation in another.
        pytest.param(OwnContext, (2, 2), id="a serializer factory of the project's"),
        pytest.param(Shouting, (1, 1), id="a method field"),
    ],
)
@pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")
async def test_what_was_read_natively_is_represented_without_a_hop(view, hops):
    await library()

    @async_new_connection
    async def read():
        return [author async for author in Author.async_objects.order_by("id")]

    authors = await read()
    listing = async_new_connection(view.as_view({"get": "list"}))
    detail = async_new_connection(view.as_view({"get": "retrieve"}))
    with count_hops() as counted:
        response = await listing(AsyncAPIRequestFactory().get("/"))
    assert counted.count == hops[0], counted.calls
    assert len(response.data) == len(authors)
    with count_hops() as counted:
        response = await detail(AsyncAPIRequestFactory().get("/"), pk=authors[0].pk)
    assert counted.count == hops[1], counted.calls
    assert response.data["id"] == authors[0].pk


list_threads = []


class RecordingList(drf_serializers.ListSerializer):
    def __init__(self, *args, **kwargs):
        list_threads.append(threading.get_ident())
        super().__init__(*args, **kwargs)


class ListedAuthors(native.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]
        list_serializer_class = RecordingList


class OwnList(Authors):
    serializer_class = ListedAuthors


async def test_a_list_serializer_of_the_projects_is_built_in_the_worker():
    await library()
    listing = async_new_connection(OwnList.as_view({"get": "list"}))
    list_threads.clear()
    with count_hops() as counted:
        response = await listing(AsyncAPIRequestFactory().get("/"))
    assert response.status_code == 200
    assert list_threads
    assert threading.get_ident() not in list_threads
    assert counted.count == 1, counted.calls


class OwnSave(Authors):
    async def aperform_create(self, serializer):
        await super().aperform_create(serializer)


@pytest.mark.parametrize(
    ("view", "hops"),
    [
        # Validation and the write's transaction for the suite's receivers
        # (two); the saved row is represented on the loop.
        pytest.param(Authors, 3, id="saved row represented on the loop"),
        # The project's save may change the serializer: the representation
        # keeps its hop.
        pytest.param(OwnSave, 4, id="a save of the project's"),
    ],
)
async def test_a_natively_saved_row_is_represented_without_a_hop(view, hops):
    create = async_new_connection(view.as_view({"post": "create"}))
    request = AsyncAPIRequestFactory().post("/", {"name": "New"}, format="json")
    with count_hops() as counted:
        response = await create(request)
    assert response.status_code == 201
    assert response.data["name"] == "New"
    assert counted.count == hops, counted.calls


async def test_a_list_prefetches_in_the_hop_that_represents_it():
    # Django's aprefetch_related_objects() is a thread hop of its own; the
    # representation, which follows it, needs one anyway.
    await library()
    real = SyncToAsync.__call__
    functions = []

    async def call(self, *args, **kwargs):
        functions.append(getattr(self.func, "__name__", None))
        return await real(self, *args, **kwargs)

    with mock.patch.object(SyncToAsync, "__call__", call), count_hops() as hops:
        response = await AsyncAPIClient().get("/books/")
    assert response.status_code == 200
    assert any(book["tags"] for book in response.data["results"])
    assert "prefetch_related_objects" not in functions, functions
    # The filter backends, then the prefetch and the representation.
    assert len(hops.calls) == 2, hops.calls


# -- Configuration errors ---------------------------------------------------------------


def test_a_synchronous_paginator_is_refused_when_the_url_is_built():
    class SyncPages(native.ListAPIView):
        queryset = Book.async_objects.all()
        serializer_class = Books.serializer_class
        pagination_class = drf_pagination.PageNumberPagination

    with pytest.raises(
        ImproperlyConfigured, match="counts and slices the queryset synchronously"
    ):
        SyncPages.as_view()


def test_the_package_app_is_required_when_the_url_is_built():
    # Without it, Model has no async_save() or _async_base_manager: native
    # reads work, writes and cascades fail.
    installed = [
        app for app in settings.INSTALLED_APPS if app != "django_async_backend"
    ]
    with (
        override_settings(INSTALLED_APPS=installed),
        pytest.raises(ImproperlyConfigured, match="django_async_backend"),
    ):
        Books.as_view({"get": "list"})


def test_a_typed_native_view_keeps_the_native_checks():
    import pydantic

    from aiodrf.contrib.typed import SchemaViewMixin

    class Title(pydantic.BaseModel):
        title: str

    class Typed(SchemaViewMixin, native.ListCreateAPIView):
        queryset = Book.async_objects.all()
        input_schema = Title

    installed = [
        app for app in settings.INSTALLED_APPS if app != "django_async_backend"
    ]
    with (
        override_settings(INSTALLED_APPS=installed),
        pytest.raises(ImproperlyConfigured, match="django_async_backend"),
    ):
        Typed.as_view()
    with pytest.raises(ImproperlyConfigured, match="synchronously"):
        Typed.as_view(pagination_class=drf_pagination.PageNumberPagination)


async def test_a_queryset_that_is_not_native_is_refused():
    class NotNative(native.ListAPIView):
        authentication_classes = []
        permission_classes = []
        queryset = Book.objects.all()
        serializer_class = Books.serializer_class

    with pytest.raises(ImproperlyConfigured, match="needs a native queryset"):
        await NotNative.as_view()(AsyncAPIRequestFactory().get("/"))


# -- The project's code off the loop (review findings) -----------------------------------


class NameOrder:
    # A filter backend that is awaited.
    async def afilter_queryset(self, request, queryset, view):
        return queryset.order_by("name")


class ScopedWithAsyncBackend(native.ListAPIView):
    authentication_classes = []
    permission_classes = []
    queryset = Author.async_objects.all()
    serializer_class = AuthorSerializer
    filter_backends = [NameOrder]

    def filter_queryset(self, queryset):
        # The view's own scope (a tenant, say), around DRF's backends.
        return super().filter_queryset(queryset).filter(name__startswith="U")


async def test_a_filter_queryset_override_applies_with_an_async_backend():
    await library()
    response = await async_new_connection(ScopedWithAsyncBackend.as_view())(
        AsyncAPIRequestFactory().get("/")
    )
    assert response.status_code == 200, response.data
    assert [row["name"] for row in response.data] == ["Ursula"]


class QueryingRouter:
    # A router of the project's may query (a tenant lookup, say).
    @async_unsafe("router on the loop")
    def db_for_write(self, model, **hints):
        return None

    def db_for_read(self, model, **hints):
        return None


@override_settings(
    DATABASE_ROUTERS=[f"{__name__}.QueryingRouter"],
    ROOT_URLCONF="tests.async_backend.urls",
)
async def test_the_projects_routers_are_asked_off_the_loop(api):
    lib, books = await library()
    updated = await api("patch", f"/books/{books[0].pk}/", {"tags": [lib["scifi"].pk]})
    assert updated.status_code == 200, updated.content
    # Without a related instance to assign: Django's own descriptor asks the
    # routers for an unsaved instance given one (see the guide).
    created = await api("post", "/authors/", {"name": "New"})
    assert created.status_code == 201, created.content
    deleted = await api("delete", f"/authors/{lib['stanislaw'].pk}/")
    assert deleted.status_code == 204


class ScifiBookSerializer(native.ModelSerializer):
    class Meta:
        model = Book
        fields = ["id", "title"]

    async def aupdate(self, instance, validated_data):
        from aiodrf.contrib.async_backend.serializers import aset_many

        instance = await super().aupdate(instance, validated_data)
        # A project's own set, from a Django queryset.
        await aset_many(instance, "tags", Tag.objects.filter(name="scifi"))
        return instance


class ScifiBooks(native.UpdateAPIView):
    authentication_classes = []
    permission_classes = []
    queryset = Book.async_objects.all()
    serializer_class = ScifiBookSerializer


async def test_set_many_takes_a_django_queryset():
    _, books = await library()
    request = AsyncAPIRequestFactory().patch("/", {"title": "t"}, format="json")
    response = await async_new_connection(ScifiBooks.as_view())(request, pk=books[0].pk)
    assert response.status_code == 200, response.data
    assert await orm(lambda: [tag.name for tag in books[0].tags.all()]) == ["scifi"]
