import asyncio
import gc
import weakref

import pytest
from asgiref.sync import sync_to_async
from django.test import TestCase, override_settings
from django.urls import path
from django.utils.asyncio import async_unsafe
from rest_framework import serializers as drf_serializers

from aiodrf import aio, serializers, viewsets
from aiodrf.aio import _save
from aiodrf.test import count_hops
from tests.base import both_transports
from tests.testapp.models import Author, Book


class UpperAuthorSerializer(serializers.ModelSerializer):
    # Only the async members are implemented.
    class Meta:
        model = Author
        fields = ["id", "name"]

    async def acreate(self, validated_data):
        return await Author.objects.acreate(name=validated_data["name"].upper())

    async def aupdate(self, instance, validated_data):
        instance.name = validated_data["name"].upper()
        await instance.asave()
        return instance


class LegacyAuthorViewSet(viewsets.ModelViewSet):
    queryset = Author.objects.all()
    serializer_class = UpperAuthorSerializer
    authentication_classes = []

    def perform_create(self, serializer):
        # Written for DRF: saves synchronously.
        serializer.save()


urlpatterns = [path("authors/", LegacyAuthorViewSet.as_view({"post": "create"}))]


class SyncSaveBridgeTests(TestCase):
    async def test_sync_save_runs_async_only_create(self):
        def save():
            serializer = UpperAuthorSerializer(data={"name": "ursula"})
            serializer.is_valid(raise_exception=True)
            return serializer.save()

        assert (await sync_to_async(save)()).name == "URSULA"

    async def test_sync_save_runs_async_only_update(self):
        author = await Author.objects.acreate(name="ursula")

        def save():
            serializer = UpperAuthorSerializer(author, data={"name": "le guin"})
            serializer.is_valid(raise_exception=True)
            return serializer.save()

        assert (await sync_to_async(save)()).name == "LE GUIN"
        assert (await Author.objects.aget(pk=author.pk)).name == "LE GUIN"

    async def test_many_true_reaches_the_child_bridge(self):
        def save():
            serializer = UpperAuthorSerializer(
                data=[{"name": "a"}, {"name": "b"}], many=True
            )
            serializer.is_valid(raise_exception=True)
            return serializer.save()

        assert [author.name for author in await sync_to_async(save)()] == ["A", "B"]


@both_transports
class _LegacyPerformCreateTests:
    @override_settings(ROOT_URLCONF=__name__)
    async def test_legacy_perform_create_runs_async_only_create(self):
        response = await self.api("post", "/authors/", data={"name": "ursula"})
        assert response.status_code == 201, response.data
        assert response.data["name"] == "URSULA"


class DepthSerializer(drf_serializers.ModelSerializer):
    class Meta:
        model = Book
        fields = ["id", "title", "author"]
        depth = 1


async def test_pair_cache_does_not_keep_dynamic_classes_alive():
    # DRF creates a ``NestedSerializer`` class per instance for ``Meta.depth``.
    book = Book(id=1, title="Title", author=Author(id=2, name="Ursula"))
    nested_classes = []

    async def serialize(times):
        for _ in range(times):
            serializer = DepthSerializer(book)
            nested_classes.append(weakref.ref(type(serializer.fields["author"])))
            await aio.data(serializer)

    await serialize(200)
    # Let the final worker future's callbacks release its result.
    await asyncio.sleep(0)
    gc.collect()
    # Global cache size can shrink when other tests' classes are collected.
    # Check this test's classes directly, without retaining them ourselves.
    assert len(nested_classes) == 200
    assert all(reference() is None for reference in nested_classes)


class AtomicSaveTests(TestCase):
    def test_update_uses_the_database_of_the_instance(self):
        author = Author(id=1, name="Ursula")
        author._state.db = "other"
        # ``Model.save`` routes with the instance as a hint; so must the
        # transaction around it.
        assert _save._write_aliases(UpperAuthorSerializer(author)) == ["other"]
        assert _save._write_aliases(UpperAuthorSerializer(data={})) == ["default"]
        # ``many=True``: an unevaluated queryset is no hint (evaluating it
        # would query outside the transaction).
        many = UpperAuthorSerializer(Author.objects.all(), many=True)
        assert _save._write_aliases(many) == ["default"]
        # Instances in hand: each one's database.
        default = Author(id=2, name="Octavia")
        default._state.db = "default"
        many = UpperAuthorSerializer([author, default], many=True)
        assert _save._write_aliases(many) == ["default", "other"]

    def test_a_backend_can_register_its_own_transaction(self):
        # django-mongodb-backend's ``transaction.atomic`` is a no-op; its own
        # is registered for its vendor by ``aiodrf.contrib.mongodb``.
        from contextlib import contextmanager
        from unittest import mock

        from django.db import connections

        entered = []

        @contextmanager
        def atomic(using):
            entered.append(using)
            yield

        serializer = UpperAuthorSerializer(data={})
        vendor = connections["default"].vendor
        with (
            mock.patch.dict(_save._ATOMIC_FACTORIES, {vendor: atomic}),
            mock.patch.object(_save.transaction, "atomic") as django_atomic,
        ):
            create = _save._atomic(serializer, lambda: "created")
            # Entered when the save runs, in the worker thread, not when the
            # save is wrapped: the factory may ask the database.
            assert entered == []
            assert create() == "created"
        assert entered == ["default"]
        django_atomic.assert_not_called()


# -- A BaseSerializer without fields (DRF's "BaseSerializer" pattern) -----------------


class Scalar(serializers.BaseSerializer):
    def to_internal_value(self, data):
        try:
            return int(data)
        except (TypeError, ValueError):
            raise drf_serializers.ValidationError(
                "Not a number.", code="number"
            ) from None

    def to_representation(self, instance):
        return instance


class DRFScalar(drf_serializers.BaseSerializer):
    to_internal_value = Scalar.to_internal_value
    to_representation = Scalar.to_representation


class GuardedScalar(Scalar):
    @async_unsafe("to_internal_value ran on the event loop")
    def to_internal_value(self, data):
        return super().to_internal_value(data)


@pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")
async def test_a_fieldless_base_serializer_validates():
    guarded = GuardedScalar(data="3")
    assert await guarded.ais_valid()
    assert guarded.validated_data == 3
    valid = Scalar(data="4")
    assert await valid.ais_valid()
    assert valid.validated_data == 4
    invalid = Scalar(data="four")
    assert not await invalid.ais_valid()
    assert invalid.errors == ["Not a number."]
    assert invalid.errors[0].code == "number"
    assert await aio.is_valid(drf_scalar := DRFScalar(data="5"))
    assert drf_scalar.validated_data == 5
    many = Scalar(data=["1", "2"], many=True)
    assert await many.ais_valid()
    assert many.validated_data == [1, 2]
    assert await Scalar(4).adata() == 4


class Initial(serializers.Serializer):
    value = serializers.IntegerField()

    @async_unsafe("get_initial ran on the event loop")
    def get_initial(self):
        return {"value": 1}


@pytest.mark.aiodrf_settings(REPRESENTATION_MODE="thread")
async def test_the_initial_representation_runs_in_the_worker():
    assert await aio.data(Initial()) == {"value": 1}
    invalid = Initial(data={"value": "x"})
    assert not await invalid.ais_valid()
    assert await invalid.adata() == {"value": 1}
    with override_settings(AIODRF={"REPRESENTATION_MODE": "inline"}):
        plain = serializers.Serializer()
        assert await aio.data(plain) == {}


class AsyncSource:
    async def get_value(self):
        return 3


class GuardedInteger(drf_serializers.IntegerField):
    @async_unsafe("a custom to_representation ran on the event loop")
    def to_representation(self, value):
        return super().to_representation(value)


@pytest.mark.aiodrf_settings(REPRESENTATION_MODE="thread")
async def test_a_custom_field_of_an_async_source_is_represented_in_the_worker():
    class Custom(serializers.Serializer):
        value = GuardedInteger(source="get_value")

        class Meta:
            model = AsyncSource

    assert await Custom(AsyncSource()).adata() == {"value": 3}


class AsyncSources(AsyncSource):
    async def get_values(self):
        return [3]


@pytest.mark.aiodrf_settings(REPRESENTATION_MODE="thread")
async def test_a_stock_field_of_an_async_source_costs_no_extra_hop():
    class Stock(serializers.Serializer):
        value = serializers.IntegerField(source="get_value")

        class Meta:
            model = AsyncSource

    with count_hops() as hops:
        assert await Stock(AsyncSource()).adata() == {"value": 3}
    # Classification and the attribute; DRF's conversion stays inline.
    assert hops.count == 2, hops.calls


@pytest.mark.aiodrf_settings(REPRESENTATION_MODE="thread")
async def test_a_container_with_a_custom_child_is_represented_in_the_worker():
    class Container(serializers.Serializer):
        values = serializers.ListField(child=GuardedInteger(), source="get_values")

        class Meta:
            model = AsyncSources

    assert await Container(AsyncSources()).adata() == {"values": [3]}


class GuardedFields(serializers.Serializer):
    name = serializers.CharField()

    @async_unsafe("get_fields ran on the event loop")
    def get_fields(self):
        return super().get_fields()


async def test_a_direct_run_validation_builds_the_fields_in_the_worker():
    assert await aio.run_validation(GuardedFields(), {"name": "ok"}) == {"name": "ok"}
    assert await GuardedFields().arun_validation({"name": "ok"}) == {"name": "ok"}
    with pytest.raises(drf_serializers.ValidationError):
        await aio.run_validation(GuardedFields(), {})
    many = GuardedFields(many=True)
    assert await aio.run_validation(many, [{"name": "a"}]) == [{"name": "a"}]


@pytest.mark.parametrize("kind", ["property", "method"])
async def test_an_async_source_of_a_primary_key_field_is_awaited(kind):
    from django.test.utils import isolate_apps

    with isolate_apps("tests.testapp"):
        if kind == "property":

            class Probe(Book):
                class Meta:
                    proxy = True
                    app_label = "testapp"

                @property
                async def writer(self):
                    return Author(pk=5)

            source = "writer"
        else:

            class Probe(Book):
                class Meta:
                    proxy = True
                    app_label = "testapp"

                async def get_writer(self):
                    return Author(pk=5)

            source = "get_writer"

        class Written(serializers.ModelSerializer):
            writer = drf_serializers.PrimaryKeyRelatedField(
                read_only=True, **({"source": source} if source != "writer" else {})
            )

            class Meta:
                model = Probe
                fields = ["id", "writer"]

        # DRF's answer for a synchronous source: the related object's pk.
        instance = Probe(pk=1, title="t", isbn="i", author_id=1)
        assert await aio.data(Written(instance)) == {"id": 1, "writer": 5}
        assert await sync_to_async(lambda: Written(instance).data)() == {
            "id": 1,
            "writer": 5,
        }


class Named(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["id", "name"]


class Shouting(Named):
    def to_representation(self, instance):
        return {**super().to_representation(instance), "name": instance.name.upper()}


class Coroutined(Named):
    later = drf_serializers.SerializerMethodField()

    class Meta(Named.Meta):
        fields = ["id", "name", "later"]

    def get_later(self, author):
        async def later():
            return 1

        return later()


def test_list_items_are_each_childs_representation():
    authors = [Author(pk=1, name="a"), Author(pk=2, name="b")]
    assert Named(authors, many=True).data == [
        {"id": 1, "name": "a"},
        {"id": 2, "name": "b"},
    ]
    assert Shouting(authors, many=True).data[1] == {"id": 2, "name": "B"}
    serializer = Named(authors, many=True)
    serializer.child.to_representation = lambda author: author.pk
    assert serializer.data == [1, 2]


def test_a_coroutine_in_a_list_item_is_refused():
    with pytest.raises(TypeError, match="later produced a coroutine"):
        Coroutined([Author(pk=1, name="a")], many=True).data  # noqa: B018


def test_a_coroutine_behind_a_primary_key_field_is_refused():
    # DRF's pk-only path (``PKOnlyObject(pk=...)``) hands the value of a
    # synchronous method returning a coroutine to the output unconverted, so
    # ``PrimaryKeyRelatedField`` stays among the checked fields.
    from django.test.utils import isolate_apps

    with isolate_apps("tests.testapp"):

        class Probe(Book):
            class Meta:
                proxy = True
                app_label = "testapp"

            def writer(self):
                return self._writer()

            async def _writer(self):
                return Author(pk=5)

        class Written(serializers.ModelSerializer):
            writer = drf_serializers.PrimaryKeyRelatedField(read_only=True)

            class Meta:
                model = Probe
                fields = ["id", "writer"]

        books = [Probe(pk=index, title="t", isbn=str(index)) for index in range(2)]
        with pytest.raises(TypeError, match="writer produced a coroutine"):
            Written(books, many=True).data  # noqa: B018


class SuperOfTheSyncMember(serializers.Serializer):
    # DRF's habit, inside the async member.
    title = serializers.CharField()

    async def ato_representation(self, instance):
        out = super().to_representation(instance)
        out["extra"] = 1
        return out


class AsyncValidated(serializers.Serializer):
    title = serializers.CharField()

    async def avalidate_title(self, value):
        return value


ON_THE_LOOP = r"was called on the event loop"


async def test_super_of_the_sync_member_in_an_async_one_names_the_fix():
    book = Book(title="t")
    with pytest.raises(RuntimeError, match=ON_THE_LOOP) as info:
        await aio.data(SuperOfTheSyncMember(book))
    message = str(info.value)
    assert "SuperOfTheSyncMember.to_representation()" in message
    assert "await super().ato_representation(" in message
    with pytest.raises(RuntimeError, match=r"await super\(\)\.ato_representation"):
        await sync_to_async(lambda: SuperOfTheSyncMember(book).data)()


async def test_a_list_of_it_names_the_fix_too():
    serializer = SuperOfTheSyncMember([Book(title="t")], many=True)
    with pytest.raises(RuntimeError, match=ON_THE_LOOP):
        await aio.data(serializer)


async def test_a_sync_bridge_on_the_loop_names_the_async_member():
    serializer = AsyncValidated(data={"title": "t"})
    with pytest.raises(RuntimeError, match=ON_THE_LOOP) as info:
        serializer.is_valid()
    # DRF's ``is_valid()`` calls the bridge of ``run_validation()``.
    assert "AsyncValidated.run_validation()" in str(info.value)
    assert "await aiodrf.aio.is_valid(serializer)" in str(info.value)
    with pytest.raises(RuntimeError, match=ON_THE_LOOP) as info:
        serializer.run_validation({"title": "t"})
    assert "arun_validation" in str(info.value)
