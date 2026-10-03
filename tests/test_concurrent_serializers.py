"""Bounded representation, fresh item state and structured cancellation."""

import asyncio
import contextvars
import gc
import threading
import weakref

import pytest
from django.core.asgi import get_asgi_application
from django.core.exceptions import ImproperlyConfigured
from django.test import override_settings
from django.urls import path
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.utils import extend_schema
from drf_spectacular.validation import validate_schema
from rest_framework import serializers as drf_serializers
from rest_framework.exceptions import ValidationError

from aiodrf import serializers
from aiodrf.aio import _classify
from aiodrf.contrib.builtin.concurrent import ConcurrentListSerializer
from aiodrf.response import Response
from aiodrf.utils import run_sync
from aiodrf.views import APIView
from tests.asgi_driver import ASGIDriver, http_scope
from tests.testapp.models import Author, Book


def list_class(item_class, *, limit=4):
    class Items(ConcurrentListSerializer):
        max_concurrency = limit

        def get_item_serializer(self, instance):
            return item_class(
                instance, context=dict(self.context), partial=self.partial
            )

    return Items


async def test_fresh_items_classify_before_materializing_fields(monkeypatch):
    classified = []
    original = _classify._async_representation

    class Item(serializers.Serializer):
        value = serializers.SerializerMethodField()

        async def get_value(self, obj):
            return obj

    def classify(serializer):
        if type(serializer) is Item:
            classified.append(True)
        return original(serializer)

    monkeypatch.setattr(_classify, "_async_representation", classify)
    candidate = list_class(Item)(range(12), child=Item())
    assert await candidate.adata() == [{"value": number} for number in range(12)]
    assert classified == [True]


async def cancel(task):
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("limit", [1, 2, 4])
@pytest.mark.parametrize("eager", [False, True])
async def test_bounded_tasks_order_and_fresh_item_context(limit, eager):
    request_id = contextvars.ContextVar("request_id", default="request")
    entered = asyncio.Queue()
    release = [asyncio.Event() for _ in range(9)]
    instances, item_tasks, finished = [], set(), []
    client = object()

    class Item(serializers.Serializer):
        value = serializers.SerializerMethodField()

        async def get_value(self, obj):
            assert self.parent is None
            assert self.root is self
            assert (
                self.context["client"] is client
            )  # No deep copying of live resources.
            assert "item" not in self.context
            assert request_id.get() == "request"
            self.context["item"] = obj
            request_id.set(str(obj))
            self.current = obj
            self.fields["value"].label = str(obj)
            instances.append(self)
            item_tasks.add(asyncio.current_task())
            await entered.put(obj)
            await release[obj].wait()
            assert self.current == self.context["item"] == obj
            assert self.fields["value"].label == request_id.get() == str(obj)
            finished.append(obj)
            return obj

    candidate = list_class(Item, limit=limit)(
        list(range(9)), child=Item(), context={"client": client}
    )
    loop = asyncio.get_running_loop()
    previous = loop.get_task_factory()
    if eager:
        loop.set_task_factory(asyncio.eager_task_factory)
    task = asyncio.create_task(candidate.adata())
    try:
        async with asyncio.timeout(3):
            first = [await entered.get() for _ in range(limit)]
            assert first == list(range(limit))
            assert len(item_tasks) == limit
            # Free the newest slot first: later work must not wait for item 0.
            release[first[-1]].set()
            assert await entered.get() == limit
            assert finished == [first[-1]]
            assert sum(not item.done() for item in item_tasks) == limit
            for gate in release:
                gate.set()
            assert await task == [{"value": index} for index in range(9)]
        assert len({id(item) for item in instances}) == 9
        assert len({id(item.fields["value"]) for item in instances}) == 9
        assert candidate.context == {"client": client}
        assert request_id.get() == "request"
        assert all(item.done() for item in item_tasks)
        assert await candidate.adata() == candidate.data  # DRF's normal data cache.
        assert len(instances) == 9
    finally:
        await cancel(task)
        loop.set_task_factory(previous)


@pytest.mark.parametrize(
    "failure",
    [ValidationError("invalid"), ValueError("broken"), asyncio.CancelledError()],
)
async def test_first_failure_cancels_and_joins_siblings_without_more_items(failure):
    entered = asyncio.Queue()
    fail = asyncio.Event()
    closing = asyncio.Event()
    release_cleanup = asyncio.Event()
    closed = []

    class Item(serializers.Serializer):
        async def ato_representation(self, obj):
            await entered.put(obj)
            try:
                if obj == 1:
                    await fail.wait()
                    raise failure
                await asyncio.Event().wait()
            finally:
                if obj == 0:
                    closing.set()
                    await release_cleanup.wait()
                closed.append(obj)

    candidate = list_class(Item, limit=2)(range(20), child=Item())
    task = asyncio.create_task(candidate.adata())
    try:
        async with asyncio.timeout(3):
            assert {await entered.get(), await entered.get()} == {0, 1}
            fail.set()
            await closing.wait()
            assert not task.done()
            release_cleanup.set()
            with pytest.raises(type(failure)) as caught:
                await task
        if not isinstance(failure, asyncio.CancelledError):
            assert caught.value is failure
        assert sorted(closed) == [0, 1]
        assert entered.empty()
        assert not hasattr(candidate, "_data")
    finally:
        release_cleanup.set()
        await cancel(task)


async def test_repeated_parent_cancellation_waits_for_item_finalizers():
    ready = asyncio.Queue()
    closing = asyncio.Queue()
    release = asyncio.Event()
    closed = []

    class Item(serializers.Serializer):
        async def ato_representation(self, obj):
            await ready.put(obj)
            try:
                await asyncio.Event().wait()
            finally:
                await closing.put(obj)
                await release.wait()
                closed.append(obj)

    candidate = list_class(Item, limit=2)(range(10), child=Item())
    task = asyncio.create_task(candidate.adata())
    try:
        async with asyncio.timeout(3):
            await ready.get()
            await ready.get()
            task.cancel()
            await closing.get()
            await closing.get()
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert sorted(closed) == [0, 1]
        assert ready.empty()
    finally:
        release.set()
        await cancel(task)


async def test_construction_fields_and_sync_iteration_stay_in_worker():
    loop_thread = threading.get_ident()
    workers = []

    class Source:
        def __iter__(self):
            workers.append(threading.get_ident())
            assert workers[-1] != loop_thread
            yield {"value": 1}

    class Item(drf_serializers.Serializer):
        value = drf_serializers.IntegerField()

        def get_fields(self):
            workers.append(threading.get_ident())
            assert workers[-1] != loop_thread
            return super().get_fields()

    class Items(list_class(Item)):
        def get_item_serializer(self, instance):
            workers.append(threading.get_ident())
            return super().get_item_serializer(instance)

    assert await Items(Source(), child=Item()).adata() == [{"value": 1}]
    assert len(workers) == 3
    assert len(set(workers)) == 1


@pytest.mark.parametrize("nested", [None, "single", "many"])
async def test_async_override_calling_super_does_not_build_custom_fields_on_loop(
    nested,
):
    loop_thread = threading.get_ident()

    class Child(serializers.Serializer):
        value = serializers.IntegerField()

        def get_fields(self):
            assert threading.get_ident() != loop_thread
            return super().get_fields()

        async def ato_representation(self, obj):
            return await super().ato_representation(obj)

    class Parent(serializers.Serializer):
        child = Child(many=nested == "many")

    item_class = Parent if nested else Child
    item = {"value": 1}
    if nested:
        item = {"child": [item] if nested == "many" else item}
    assert await list_class(item_class)([item], child=item_class()).adata() == [item]


@pytest.mark.aiodrf_settings(REPRESENTATION_MODE="thread")
@pytest.mark.django_db(transaction=True)
async def test_queryset_related_manager_lazy_fields_and_custom_factory(
    worker_connections,
):
    author = await Author.objects.acreate(name="Ursula")
    await Book.objects.acreate(title="Earthsea", author=author)

    class Item(serializers.ModelSerializer):
        author_name = serializers.CharField(source="author.name")

        class Meta:
            model = Book
            fields = ["title", "author_name"]

        def __init__(self, *args, prefix, **kwargs):
            self.prefix = prefix
            super().__init__(*args, **kwargs)

        async def ato_representation(self, obj):
            body = await super().ato_representation(obj)
            return {
                "title": self.prefix + body["title"],
                "author_name": body["author_name"],
            }

    class Items(ConcurrentListSerializer):
        def get_item_serializer(self, instance):
            return Item(instance, prefix="Book: ", context=dict(self.context))

    for source in (Book.objects.order_by("pk"), author.books):
        assert await Items(source, child=Item(prefix="")).adata() == [
            {"title": "Book: Earthsea", "author_name": "Ursula"}
        ]


async def test_validation_and_creation_remain_sequential_and_do_not_use_factory():
    calls = []

    class Item(serializers.Serializer):
        value = serializers.IntegerField()

        async def validate_value(self, value):
            calls.append(("validate", value))
            await asyncio.sleep(0)
            assert calls[-1] == ("validate", value)
            return value

        async def acreate(self, values):
            calls.append(("create", values["value"]))
            await asyncio.sleep(0)
            assert calls[-1] == ("create", values["value"])
            return values

        class Meta:
            list_serializer_class = (
                ConcurrentListSerializer  # No representation factory.
            )

    values = [{"value": index} for index in range(4)]
    candidate = Item(data=values, many=True)
    assert isinstance(candidate, ConcurrentListSerializer)
    assert await candidate.ais_valid(), candidate.errors
    assert await candidate.asave() == values
    assert calls == [
        (phase, index) for phase in ("validate", "create") for index in range(4)
    ]
    with pytest.raises(NotImplementedError, match="get_item_serializer"):
        await candidate.adata()


def test_sync_bridge_and_spectacular_keep_normal_list_shape():
    class Items(ConcurrentListSerializer):
        def get_item_serializer(self, instance):
            return Item(instance, context=dict(self.context))

    class Item(serializers.Serializer):
        value = serializers.SerializerMethodField()

        async def get_value(self, obj) -> int:
            return obj["value"]

        class Meta:
            list_serializer_class = Items

    assert Item([{"value": 2}], many=True).data == [{"value": 2}]

    class View(APIView):
        @extend_schema(responses=Item(many=True))
        async def get(self, request):
            raise AssertionError("Schema generation must not serialize items.")

    document = SchemaGenerator(patterns=[path("items/", View.as_view())]).get_schema(
        public=True
    )
    validate_schema(document)
    schema = document["paths"]["/items/"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]["schema"]
    assert schema == {"type": "array", "items": {"$ref": "#/components/schemas/Item"}}


async def test_asgi_disconnect_closes_all_inflight_item_work():
    started = asyncio.Queue()
    closed = []

    class Item(serializers.Serializer):
        async def ato_representation(self, obj):
            await started.put(obj)
            try:
                await asyncio.Event().wait()
            finally:
                closed.append(obj)

    class View(APIView):
        authentication_classes = []
        permission_classes = []

        async def get(self, request):
            serializer = list_class(Item, limit=3)(range(30), child=Item())
            return Response(await serializer.adata())

    with override_settings(ROOT_URLCONF=(path("", View.as_view()),), MIDDLEWARE=[]):
        async with (
            ASGIDriver(get_asgi_application(), http_scope()) as driver,
            asyncio.timeout(3),
        ):
            await driver.incoming.put({"type": "http.request", "body": b""})
            assert {await started.get(), await started.get(), await started.get()} == {
                0,
                1,
                2,
            }
            await driver.incoming.put({"type": "http.disconnect"})
            await driver.finish()
    assert sorted(closed) == [0, 1, 2]
    assert started.empty()


async def test_result_does_not_keep_item_serializers_or_request_context_alive():
    references = []

    class Resource:
        pass

    class Item(serializers.Serializer):
        value = serializers.SerializerMethodField()

        async def get_value(self, obj):
            references.append(weakref.ref(self))
            return obj

    resource = Resource()
    resource_ref = weakref.ref(resource)
    candidate = list_class(Item)(
        range(40), child=Item(), context={"resource": resource}
    )
    result = await candidate.adata()
    # ReturnList normally keeps the outer serializer; the plain item results
    # must not retain the fresh serializers, even while that list is alive.
    # Future notification can wake the loop before the worker has dropped its
    # returned object. Finish that worker turn before checking settled ownership.
    await run_sync(lambda: None)()
    gc.collect()
    assert len(references) == 40
    assert all(ref() is None for ref in references)
    del result, candidate, resource
    gc.collect()
    assert resource_ref() is None


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, "2", None])
def test_invalid_concurrency_fails_at_construction(limit):
    with pytest.raises(ValueError, match="positive integer"):
        list_class(serializers.Serializer, limit=limit)(child=serializers.Serializer())


@pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")
@pytest.mark.parametrize("kind", ["bound", "reused", "wrong"])
async def test_factory_must_return_a_fresh_unbound_serializer(kind):
    child = serializers.Serializer()
    reused = serializers.Serializer()

    class Items(ConcurrentListSerializer):
        max_concurrency = 1

        def get_item_serializer(self, instance):
            return {"bound": child, "reused": reused, "wrong": object()}[kind]

    with pytest.raises(TypeError, match="serializer"):
        await Items([{}, {}], child=child).adata()


def test_async_factory_is_rejected_without_creating_a_coroutine():
    class Items(ConcurrentListSerializer):
        async def get_item_serializer(self, instance):
            raise AssertionError("Must not be called.")

    with pytest.raises(ImproperlyConfigured, match="must be synchronous"):
        Items(child=serializers.Serializer())


@pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")
async def test_empty_input_does_not_need_a_factory():
    assert (
        await ConcurrentListSerializer([], child=serializers.Serializer()).adata() == []
    )


async def test_simultaneous_requests_do_not_share_factory_context_or_item_state():
    tenant = contextvars.ContextVar("tenant", default="outside")
    ready = asyncio.Barrier(4)

    class Item(serializers.Serializer):
        value = serializers.SerializerMethodField()

        async def get_value(self, obj):
            await ready.wait()
            assert tenant.get() == self.context["tenant"]
            return [self.context["tenant"], obj]

    class Items(list_class(Item, limit=2)):
        def get_item_serializer(self, instance):
            assert tenant.get() == self.context["tenant"]
            return super().get_item_serializer(instance)

    async def request(name):
        tenant.set(name)
        return await Items(range(4), child=Item(), context={"tenant": name}).adata()

    async with asyncio.timeout(3):
        first, second = await asyncio.gather(request("first"), request("second"))
    assert first == [{"value": ["first", index]} for index in range(4)]
    assert second == [{"value": ["second", index]} for index in range(4)]
    assert tenant.get() == "outside"


async def test_representation_validation_error_keeps_drf_http_status_and_body():
    class Item(serializers.Serializer):
        async def ato_representation(self, obj):
            raise ValidationError({"detail": "not available"})

    class View(APIView):
        authentication_classes = []
        permission_classes = []

        async def get(self, request):
            return Response(await list_class(Item)([1, 2], child=Item()).adata())

    with override_settings(ROOT_URLCONF=(path("", View.as_view()),), MIDDLEWARE=[]):
        async with ASGIDriver(get_asgi_application(), http_scope()) as driver:
            await driver.incoming.put({"type": "http.request", "body": b""})
            await driver.finish()
    assert driver.sent[0]["status"] == 400
    body = b"".join(message.get("body", b"") for message in driver.sent[1:])
    assert body == b'{"detail":"not available"}'


@pytest.mark.parametrize("payload", [[], [{"value": "bad"}], [{"value": 1}, {}]])
async def test_validation_errors_keep_sequential_drf_contract(payload):
    class Item(serializers.Serializer):
        value = serializers.IntegerField()

    reference = Item(data=payload, many=True, allow_empty=False)
    candidate = list_class(Item)(data=payload, child=Item(), allow_empty=False)
    assert await candidate.ais_valid() == await reference.ais_valid()
    assert candidate.errors == reference.errors


@pytest.mark.parametrize("backend", ["msgspec", "pydantic"])
async def test_typed_adapters_keep_their_output_schema(backend):
    import msgspec
    import pydantic

    from aiodrf.contrib.typed import adapt

    class Struct(msgspec.Struct):
        value: int

    class Model(pydantic.BaseModel):
        value: int = pydantic.Field(serialization_alias="label")

    item_class = adapt(Struct if backend == "msgspec" else Model)
    candidate = list_class(item_class)([{"value": 1}], child=item_class())
    assert await candidate.adata() == [
        {"value" if backend == "msgspec" else "label": 1}
    ]


@pytest.mark.parametrize("asynchronous", [False, True])
async def test_fieldless_base_serializers_work(asynchronous):
    if asynchronous:

        class Item(serializers.BaseSerializer):
            async def ato_representation(self, obj):
                return obj * 2
    else:

        class Item(drf_serializers.BaseSerializer):
            def to_representation(self, obj):
                return obj * 2

    assert await list_class(Item)([1, 2], child=Item()).adata() == [2, 4]


@pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")
async def test_sync_factory_returning_coroutine_fails_without_leaking_it():
    async def factory():
        raise AssertionError("Never awaited.")

    class Items(ConcurrentListSerializer):
        def get_item_serializer(self, instance):
            return factory()

    with pytest.raises(TypeError, match="unbound serializer"):
        await Items([1], child=serializers.Serializer()).adata()
    gc.collect()


class Named(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]


@pytest.mark.django_db
@pytest.mark.parametrize("backend", ["drf", "msgspec", "python"])
async def test_a_compiled_child_still_goes_through_the_lists_items(backend):
    seen = []

    class Recorded(serializers.ModelSerializer):
        class Meta:
            model = Author
            fields = ["id", "name"]

    class Items(ConcurrentListSerializer):
        def get_item_serializer(self, instance):
            seen.append(instance.pk)
            return Named(instance, context=dict(self.context))

    authors = [Author(pk=1, name="Ada"), Author(pk=2, name="Bo")]
    with override_settings(FASTDRF={"SERIALIZER_BACKEND": backend}, AIODRF={}):
        data = await Items(authors, child=Recorded()).adata()
    assert data == [{"id": 1, "name": "Ada"}, {"id": 2, "name": "Bo"}]
    assert sorted(seen) == [1, 2]
