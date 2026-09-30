"""
Where serializer code runs: the project's code is not called on the event
loop, and members that must be awaited are not handed to DRF's synchronous
code, which would call them without awaiting.
"""

import functools
import os

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.test.utils import isolate_apps
from django.utils.asyncio import async_unsafe
from rest_framework import serializers as drf
from rest_framework.permissions import AllowAny

from aiodrf import aio, generics, serializers
from aiodrf.test import AsyncAPIRequestFactory
from tests.testapp.models import Author, Book, Edition

CALLS = []


@async_unsafe("callable default on the loop")
def blocking_default():
    CALLS.append("default")
    return "x"


class ReadOnlyDefault(serializers.Serializer):
    name = serializers.CharField()
    stamp = serializers.CharField(read_only=True, default=blocking_default)


async def test_the_default_of_a_read_only_field_is_not_called_on_the_loop():
    # DRF calls it for the serializer's validators (``_read_only_defaults``).
    CALLS.clear()
    serializer = ReadOnlyDefault(data={"name": "a"})
    assert await serializer.ais_valid(), serializer.errors
    assert CALLS == ["default"]


class Child(drf.Serializer):
    a = drf.CharField()


@pytest.mark.parametrize("base", [serializers.Serializer, drf.Serializer])
async def test_the_default_of_a_nested_serializer_is_not_called_on_the_loop(base):
    Outer = type(
        "Outer",
        (base,),
        {
            "name": drf.CharField(),
            "child": Child(required=False, default=blocking_default),
        },
    )
    serializer = Outer(data={"name": "a"})
    assert await aio.is_valid(serializer), serializer.errors
    assert serializer.validated_data == {"name": "a", "child": "x"}


async def avalidate_name(self, value):
    return value


# The same defaults, on a serializer with an async hook: its validation is
# walked stage by stage on the loop, and the defaults must still go to the
# worker.


@pytest.mark.parametrize("base", [serializers.Serializer, drf.Serializer])
async def test_a_read_only_default_is_not_called_on_the_loop_with_async_hooks(base):
    Hooked = type(
        "Hooked",
        (base,),
        {
            "name": drf.CharField(),
            "stamp": drf.CharField(read_only=True, default=blocking_default),
            "avalidate_name": avalidate_name,
        },
    )
    CALLS.clear()
    serializer = Hooked(data={"name": "a"})
    assert await aio.is_valid(serializer), serializer.errors
    assert CALLS == ["default"]


@pytest.mark.parametrize("base", [serializers.Serializer, drf.Serializer])
async def test_a_nested_default_is_not_called_on_the_loop_with_async_hooks(base):
    Outer = type(
        "Outer",
        (base,),
        {
            "name": drf.CharField(),
            "child": Child(required=False, default=blocking_default),
            "avalidate_name": avalidate_name,
        },
    )
    serializer = Outer(data={"name": "a"})
    assert await aio.is_valid(serializer), serializer.errors
    assert serializer.validated_data == {"name": "a", "child": "x"}


class AsyncChild(drf.Serializer):
    a = drf.CharField()

    async def avalidate_a(self, value):
        return value


@pytest.mark.parametrize("base", [serializers.Serializer, drf.Serializer])
async def test_the_default_of_an_async_nested_serializer_is_not_called_on_the_loop(
    base,
):
    Outer = type(
        "Outer",
        (base,),
        {
            "name": drf.CharField(),
            "child": AsyncChild(required=False, default=blocking_default),
        },
    )
    serializer = Outer(data={"name": "a"})
    assert await aio.is_valid(serializer), serializer.errors
    assert serializer.validated_data == {"name": "a", "child": "x"}


class ValueFromHeader(AsyncChild):
    @async_unsafe("get_value on the loop")
    def get_value(self, dictionary):
        return super().get_value(dictionary)


async def test_get_value_of_an_async_nested_serializer_is_not_called_on_the_loop():
    Outer = type("Outer", (serializers.Serializer,), {"child": ValueFromHeader()})
    serializer = Outer(data={"child": {"a": "x"}})
    assert await aio.is_valid(serializer), serializer.errors
    assert serializer.validated_data == {"child": {"a": "x"}}


class CheckedCode(drf.CharField):
    # A check against the database, for example.
    @async_unsafe("run_validators on the loop")
    def run_validators(self, value):
        super().run_validators(value)


@pytest.mark.parametrize("base", [serializers.Serializer, drf.Serializer])
async def test_a_field_run_validators_is_not_called_on_the_loop_with_async_hooks(
    base,
):
    async def avalidate(self, attrs):
        return attrs

    Hooked = type("Hooked", (base,), {"code": CheckedCode(), "avalidate": avalidate})
    serializer = Hooked(data={"code": "a"})
    assert await aio.is_valid(serializer), serializer.errors


class Files(serializers.Serializer):
    path = serializers.FilePathField(path=os.path.dirname(__file__))


async def test_a_file_path_field_lists_its_directory_in_the_worker(monkeypatch):
    real = os.scandir

    @async_unsafe("directory listed on the loop")
    def scandir(*args, **kwargs):
        return real(*args, **kwargs)

    monkeypatch.setattr(os, "scandir", scandir)
    serializer = Files(data={"path": __file__})
    assert await serializer.ais_valid(), serializer.errors


@async_unsafe("model choices on the loop")
def currency_choices():
    # In a project: ``[(c.code, c.name) for c in Currency.objects.all()]``.
    return [("usd", "USD"), ("eur", "EUR")]


@async_unsafe("limit_choices_to on the loop")
def active_authors():
    return {"name__startswith": "a"}


@pytest.mark.parametrize("field", ["currency", "author"])
@isolate_apps("tests.testapp")
async def test_model_fields_whose_options_are_callables_are_built_in_the_worker(field):
    # Isolated: a relation to Author would take part in deleting authors.
    class Price(models.Model):
        currency = models.CharField(max_length=3, choices=currency_choices)
        author = models.ForeignKey(
            Author, models.CASCADE, null=True, limit_choices_to=active_authors
        )

        class Meta:
            app_label = "testapp"

        def __str__(self):
            return self.currency

    class PriceSerializer(serializers.ModelSerializer):
        class Meta:
            model = Price
            fields = [field]

    serializer = PriceSerializer(data={"currency": "usd", "author": None})
    assert await serializer.ais_valid(), serializer.errors


class Scoping(models.Manager):
    # The project's scope, such as the request's tenant.
    @async_unsafe("default manager on the loop")
    def get_queryset(self):
        return super().get_queryset()


class Limited(models.CharField):
    """A model field class of the project's, whose attributes DRF reads."""

    @property
    @async_unsafe("model field read on the loop")
    def max_length(self):
        return self._max_length

    @max_length.setter
    def max_length(self, value):
        self._max_length = value


@pytest.fixture(params=["owner", "code"])
@isolate_apps("tests.testapp")
def project_built(request):
    """A serializer of a model field whose building runs the project's code."""

    class Owner(models.Model):
        objects = Scoping()

        class Meta:
            app_label = "testapp"

        def __str__(self):
            return str(self.pk)

    if request.param == "owner":
        # A constant limit_choices_to is applied through the related model's
        # default manager, when DRF builds the relation's queryset.
        field = models.ForeignKey(
            Owner, models.CASCADE, null=True, limit_choices_to={"pk": 1}
        )
        value = None
    else:
        field = Limited(max_length=3)
        value = "usd"
    meta = type("Meta", (), {"app_label": "testapp"})
    model = type(
        "Price", (models.Model,), {"f": field, "Meta": meta, "__module__": __name__}
    )
    meta = type("Meta", (), {"model": model, "fields": ["f"]})
    return type("PriceSerializer", (serializers.ModelSerializer,), {"Meta": meta}), {
        "f": value
    }


async def test_model_fields_that_run_the_projects_code_when_built_are_built_in_the_worker(
    project_built,
):
    serializer_class, data = project_built
    serializer = serializer_class(data=data)
    assert await serializer.ais_valid(), serializer.errors


class EditionSummary(serializers.ModelSerializer):
    summary = serializers.CharField(source="book.asummary")

    class Meta:
        model = Edition
        fields = ["summary"]


async def test_an_async_method_behind_a_relation_is_awaited():
    edition = Edition(book=Book(title="T", pages=3))
    assert await aio.data(EditionSummary(edition)) == {"summary": "T (3 pages)"}


class RejectsAsync(drf.Serializer):
    name = drf.CharField()

    async def validate_name(self, value):
        raise drf.ValidationError("rejected")


class LoggedIsValid(RejectsAsync):
    def is_valid(self, *, raise_exception=False):
        # Logging or metrics, written for DRF.
        return super().is_valid(raise_exception=raise_exception)


async def test_a_sync_is_valid_of_drf_cannot_skip_async_validation():
    assert await aio.is_valid(RejectsAsync(data={"name": "x"})) is False
    with pytest.raises(ImproperlyConfigured, match="async validation"):
        await aio.is_valid(LoggedIsValid(data={"name": "x"}))


class LoggedIsValidOfAiodrf(serializers.Serializer):
    name = serializers.CharField()

    async def validate_name(self, value):
        raise drf.ValidationError("rejected")

    def is_valid(self, *, raise_exception=False):
        return super().is_valid(raise_exception=raise_exception)


async def test_a_sync_is_valid_of_aiodrf_awaits_through_its_bridge():
    serializer = LoggedIsValidOfAiodrf(data={"name": "x"})
    assert await aio.is_valid(serializer) is False
    assert serializer.errors == {"name": ["rejected"]}


class DRFBeforeTheBridge(drf.Serializer, serializers.AsyncSerializerMixin):
    # DRF's ``is_valid`` precedes the mixin's bridge: ``super()`` reaches DRF's.
    name = drf.CharField()

    async def validate_name(self, value):
        raise drf.ValidationError("rejected")

    def is_valid(self, *, raise_exception=False):
        return super().is_valid(raise_exception=raise_exception)


async def test_a_bridge_after_drfs_is_valid_does_not_waive_the_guard():
    with pytest.raises(ImproperlyConfigured, match="async validation"):
        await aio.is_valid(DRFBeforeTheBridge(data={"name": "x"}))


class UpperCaseAcreate(drf.ModelSerializer):
    class Meta:
        model = Author
        fields = ["name"]

    async def acreate(self, validated_data):
        return await Author.objects.acreate(name=validated_data["name"].upper())


class AsyncDefCreate(drf.ModelSerializer):
    class Meta:
        model = Author
        fields = ["name"]

    async def create(self, validated_data):
        return await Author.objects.acreate(**validated_data)


class Create(generics.CreateAPIView):
    authentication_classes = []
    permission_classes = [AllowAny]
    queryset = Author.objects.all()


class PerformCreate(Create):
    def perform_create(self, serializer):
        serializer.save()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("serializer_class", [UpperCaseAcreate, AsyncDefCreate])
async def test_a_sync_perform_create_cannot_skip_async_creation(serializer_class):
    request = AsyncAPIRequestFactory().post("/", {"name": "ada"}, format="json")
    view = Create.as_view(serializer_class=serializer_class)
    assert (await view(request)).status_code == 201

    request = AsyncAPIRequestFactory().post("/", {"name": "bob"}, format="json")
    view = PerformCreate.as_view(serializer_class=serializer_class)
    with pytest.raises(ImproperlyConfigured, match="perform_create"):
        await view(request)
    assert await Author.objects.acount() == 1


class LoggedSave(UpperCaseAcreate):
    def save(self, **kwargs):
        return super().save(**kwargs)


@pytest.mark.django_db(transaction=True)
async def test_a_sync_save_of_drf_cannot_skip_async_creation():
    serializer = LoggedSave(data={"name": "ada"})
    assert await aio.is_valid(serializer)
    with pytest.raises(ImproperlyConfigured, match="acreate"):
        await aio.save(serializer)
    assert await Author.objects.acount() == 0


class UpperCaseAcreateOfAiodrf(serializers.ModelSerializer):
    class Meta:
        model = Author
        fields = ["name"]

    async def acreate(self, validated_data):
        return await Author.objects.acreate(name=validated_data["name"].upper())


@pytest.mark.django_db(transaction=True)
async def test_a_sync_perform_create_saves_an_aiodrf_serializer_through_its_bridge():
    request = AsyncAPIRequestFactory().post("/", {"name": "ada"}, format="json")
    view = PerformCreate.as_view(serializer_class=UpperCaseAcreateOfAiodrf)
    assert (await view(request)).data == {"name": "ADA"}


# -- synchronous wrappers of coroutine functions ------------------------------------------
#
# ``functools.wraps`` around an ``async def`` hides a synchronous function
# that returns the coroutine. Its own code (the prologue) may block: it runs
# in the worker, and the coroutine it returns is awaited on the loop.


@async_unsafe("a wrapper's prologue on the loop")
def blocking_prologue():
    CALLS.append("prologue")


def prologue(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        blocking_prologue()
        return func(*args, **kwargs)

    return wrapper


async def reject_bad(value):
    if value == "bad":
        raise drf.ValidationError("bad value")


not_bad = prologue(reject_bad)


class NotBad:
    @prologue
    async def __call__(self, value):
        await reject_bad(value)


def wrapped_hooks(base):
    async def validate_name(self, value):
        return value.upper()

    async def avalidate(self, attrs):
        return {**attrs, "checked": True}

    return {
        "Wrapped validator": {"name": drf.CharField(validators=[not_bad])},
        "Wrapped validator class": {"name": drf.CharField(validators=[NotBad()])},
        "Wrapped field hook": {
            "name": drf.CharField(),
            "validate_name": prologue(validate_name),
        },
        "Wrapped hook": {"name": drf.CharField(), "avalidate": prologue(avalidate)},
    }


@pytest.mark.parametrize("base", [serializers.Serializer, drf.Serializer])
@pytest.mark.parametrize(
    "case",
    [
        "Wrapped validator",
        "Wrapped validator class",
        "Wrapped field hook",
        "Wrapped hook",
    ],
)
async def test_a_wrapper_of_an_async_validation_hook_runs_its_prologue_in_the_worker(
    base, case
):
    cls = type(case.replace(" ", ""), (base,), wrapped_hooks(base)[case])
    expected = {
        "Wrapped validator": {"name": "ok"},
        "Wrapped validator class": {"name": "ok"},
        "Wrapped field hook": {"name": "OK"},
        "Wrapped hook": {"name": "ok", "checked": True},
    }[case]
    CALLS.clear()
    serializer = cls(data={"name": "ok"})
    assert await aio.is_valid(serializer), serializer.errors
    assert serializer.validated_data == expected
    assert CALLS == ["prologue"]
    if "validator" in case:
        serializer = cls(data={"name": "bad"})
        assert not await aio.is_valid(serializer)
        assert serializer.errors == {"name": ["bad value"]}


@pytest.mark.parametrize("base", [serializers.Serializer, drf.Serializer])
async def test_a_wrapper_of_an_async_method_field_runs_its_prologue_in_the_worker(base):
    async def get_summary(self, obj):
        return f"{obj.title}!"

    cls = type(
        "Summarized",
        (base,),
        {"summary": drf.SerializerMethodField(), "get_summary": prologue(get_summary)},
    )
    CALLS.clear()
    assert await aio.data(cls(Book(title="T"))) == {"summary": "T!"}
    assert CALLS == ["prologue"]


@pytest.mark.aiodrf_settings(REPRESENTATION_MODE="thread")
@pytest.mark.parametrize("base", [serializers.ModelSerializer, drf.ModelSerializer])
async def test_a_wrapper_of_an_async_model_method_runs_its_prologue_in_the_worker(
    monkeypatch, base
):
    monkeypatch.setattr(Book, "wrapped_summary", prologue(Book.asummary), raising=False)
    meta = type("Meta", (), {"model": Book, "fields": ["summary"]})
    attrs = {"summary": drf.CharField(source="wrapped_summary"), "Meta": meta}
    cls = type("Summarized", (base,), attrs)
    CALLS.clear()
    assert await aio.data(cls(Book(title="T", pages=3))) == {"summary": "T (3 pages)"}
    assert CALLS == ["prologue"]


class Gauge:
    # Not a model: an async property aiodrf cannot see statically.
    @property
    async def reading(self):
        return 1


class Readings(serializers.Serializer):
    readings = serializers.SerializerMethodField()

    def get_readings(self, gauge):
        return {"latest": [gauge.reading]}


@pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")
async def test_a_coroutine_nested_in_a_method_fields_value_is_refused():
    with pytest.raises(TypeError, match="produced a coroutine"):
        await aio.data(Readings(Gauge()))


class Reading(serializers.Serializer):
    name = serializers.CharField(default="gauge")
    count = serializers.IntegerField(default=1)
    reading = serializers.SerializerMethodField()

    def get_reading(self, gauge):
        return gauge.reading


@pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")
async def test_a_coroutine_among_plain_values_is_refused_and_closed():
    gauge = Gauge()
    with pytest.raises(TypeError, match=r"Reading\.reading produced a coroutine"):
        await aio.data(Reading(gauge))


class Passed(serializers.Serializer):
    # Converting fields cannot pass a coroutine on; these can.
    name = serializers.CharField(default="gauge")
    reading = serializers.ReadOnlyField()


class Overridden(serializers.Serializer):
    name = serializers.CharField(default="gauge")
    reading = serializers.CharField()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["reading"].to_representation = lambda value: value


class Named(serializers.Serializer):
    name = serializers.CharField()


class Mixed(serializers.Serializer):
    first = Named(source="*")
    many = Named(source="names", many=True)
    reading = serializers.SerializerMethodField()

    def get_reading(self, gauge):
        return gauge.reading


class NamedGauge(Gauge):
    name = "gauge"
    names = ({"name": "a"},)


class AddsAfterDRF:
    # Between aiodrf's check and DRF's representation: what it adds is checked.
    def to_representation(self, instance):
        ret = super().to_representation(instance)
        ret["name"] = instance.reading
        return ret


class Added(serializers.AsyncSerializerMixin, AddsAfterDRF, drf.Serializer):
    name = serializers.CharField(default="gauge")


def test_the_mixin_order_is_what_the_fixture_needs():
    assert Added.__mro__.index(AddsAfterDRF) > Added.__mro__.index(
        serializers.AsyncSerializerMixin
    )


@pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")
@pytest.mark.parametrize("cls", [Passed, Overridden, Mixed, Added])
async def test_fields_that_pass_values_on_are_checked(cls):
    field = "name" if cls is Added else "reading"
    with pytest.raises(TypeError, match=f"{cls.__qualname__}.{field} produced"):
        await aio.data(cls(NamedGauge()))
    # Each item of a list is checked by the same child.
    with pytest.raises(TypeError, match="produced a coroutine"):
        await aio.data(cls([NamedGauge(), NamedGauge()], many=True))


class Cyclic(serializers.Serializer):
    rows = serializers.SerializerMethodField()

    def get_rows(self, obj):
        rows = [1]
        rows.append(rows)
        return rows


@pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")
async def test_a_cyclic_method_value_is_inspected_once():
    # The renderer reports the cycle; the inspection does not loop on it.
    data = await aio.data(Cyclic({}))
    assert data["rows"][1] is data["rows"]


class AsyncHex:
    # What ``UUIDField(format="hex")`` reads, as an async property.
    @property
    async def hex(self):
        return "abc"


class TokenGauge(NamedGauge):
    token = AsyncHex()


class Hexed(serializers.Serializer):
    token = serializers.UUIDField(format="hex")


class DRFReading(drf.Serializer):
    # DRF's own serializer checks nothing of its output.
    reading = drf.SerializerMethodField()

    def get_reading(self, gauge):
        return gauge.reading


class WrapsDRF(serializers.Serializer):
    name = serializers.CharField()
    inner = DRFReading(source="*")


class Growing(serializers.Serializer):
    name = serializers.CharField()

    def to_representation(self, instance):
        if getattr(instance, "extra", False) and "reading" not in self.fields:
            self.fields["reading"] = serializers.SerializerMethodField()
        return super().to_representation(instance)

    def get_reading(self, gauge):
        return gauge.reading


class ExtraGauge(NamedGauge):
    extra = True


@pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")
@pytest.mark.parametrize(
    "make",
    [
        pytest.param(lambda: Hexed(TokenGauge()), id="uuid format"),
        pytest.param(lambda: WrapsDRF(NamedGauge()), id="drf nested"),
        pytest.param(
            lambda: Growing([NamedGauge(), ExtraGauge()], many=True), id="fields grow"
        ),
    ],
)
async def test_what_the_representation_passes_on_is_checked(make):
    with pytest.raises(TypeError, match="produced a coroutine"):
        await aio.data(make())
