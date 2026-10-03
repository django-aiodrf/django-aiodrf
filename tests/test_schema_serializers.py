"""Schema-first serializers (msgspec Structs, pydantic models)."""

import gc
import weakref

import msgspec
import pydantic
import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.http import QueryDict
from django.test import override_settings
from fastdrf import typed as fastdrf_typed
from pydantic.alias_generators import to_camel

from aiodrf.contrib import typed
from aiodrf.contrib.msgspec import MsgspecSerializer
from aiodrf.contrib.pydantic import PydanticSerializer
from tests.base import list_errors


class Positive(msgspec.Struct):
    value: int
    note: str = ""

    def __post_init__(self):
        if self.value < 0:
            raise ValueError("positive required")


class PositivePatch(msgspec.Struct):
    value: int | msgspec.UnsetType = msgspec.UNSET
    note: str | msgspec.UnsetType = msgspec.UNSET

    def __post_init__(self):
        if self.value is not msgspec.UNSET and self.value < 0:
            raise ValueError("positive required")


class PositiveSerializer(MsgspecSerializer):
    class Meta:
        schema = Positive


class PositiveWithPatchSerializer(MsgspecSerializer):
    class Meta:
        schema = Positive
        partial_schema = PositivePatch


def test_partial_validation_never_drops_schema_invariants():
    # A derived partial Struct would not run ``__post_init__``.
    assert not PositiveSerializer(data={"value": -1}).is_valid()
    with pytest.raises(ImproperlyConfigured, match="partial_schema"):
        PositiveSerializer(data={"value": -1}, partial=True).is_valid()

    serializer = PositiveWithPatchSerializer(data={"value": -1}, partial=True)
    assert not serializer.is_valid()
    serializer = PositiveWithPatchSerializer(data={"note": "x"}, partial=True)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == {"note": "x"}


class Plain(msgspec.Struct):
    value: int
    note: str = ""


def test_partial_schema_is_derived_when_nothing_can_be_lost():
    class PlainSerializer(MsgspecSerializer):
        class Meta:
            schema = Plain

    serializer = PlainSerializer(data={"note": "x"}, partial=True)
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == {"note": "x"}


class Checked(pydantic.BaseModel):
    value: int

    @pydantic.field_validator("value")
    @classmethod
    def positive(cls, value):
        if value < 0:
            raise ValueError("positive required")
        return value


def test_pydantic_models_with_validators_need_a_partial_schema():
    class CheckedSerializer(PydanticSerializer):
        class Meta:
            schema = Checked

    with pytest.raises(ImproperlyConfigured, match="partial_schema"):
        CheckedSerializer(data={}, partial=True).is_valid()


class CountedDefault(pydantic.BaseModel):
    count: int = pydantic.Field(default=0, validate_default=True)
    note: str = ""


def test_pydantic_fields_validating_defaults_need_a_partial_schema():
    class CountedSerializer(PydanticSerializer):
        class Meta:
            schema = CountedDefault

    with pytest.raises(ImproperlyConfigured, match="validates defaults"):
        CountedSerializer(data={"note": "x"}, partial=True).is_valid()


class Aliased(pydantic.BaseModel):
    value: int = pydantic.Field(serialization_alias="public_value")


class AliasedSerializer(PydanticSerializer):
    class Meta:
        schema = Aliased


def test_pydantic_output_uses_the_names_of_its_schema():
    body, _ = AliasedSerializer().backend.json_schema(
        Aliased, ref_prefix="#/components/schemas/", direction="response"
    )
    assert list(body["properties"]) == ["public_value"]
    assert AliasedSerializer(Aliased(value=1)).data == {"public_value": 1}
    assert AliasedSerializer([Aliased(value=1)], many=True).data == [
        {"public_value": 1}
    ]
    assert list(AliasedSerializer().fields) == ["public_value"]


def test_adapted_serializer_classes_are_bounded():
    # The adapted class refers to its schema, so a weak cache would never
    # let go of either; the cache is bounded instead.
    references = []
    for index in range(fastdrf_typed.SCHEMA_CACHE_SIZE + 50):
        schema = msgspec.defstruct(f"Transient{index}", [("value", int)])
        assert typed.adapt(schema) is typed.adapt(schema)
        references.append(weakref.ref(schema))
        del schema
    gc.collect()
    alive = sum(reference() is not None for reference in references)
    assert alive <= fastdrf_typed.SCHEMA_CACHE_SIZE


def test_one_class_per_schema_under_concurrent_first_use():
    from concurrent.futures import ThreadPoolExecutor

    schema = msgspec.defstruct("RacedSchema", [("value", int)])
    with ThreadPoolExecutor(16) as pool:
        classes = set(pool.map(lambda _: typed.adapt(schema), range(64)))
    assert len(classes) == 1


class Tagged(msgspec.Struct):
    name: str
    tags: list[str] = []


def test_form_input_keeps_repeated_values():
    class TaggedSerializer(MsgspecSerializer):
        class Meta:
            schema = Tagged

    serializer = TaggedSerializer(data=QueryDict("name=a&tags=x&tags=y"))
    assert serializer.is_valid(), serializer.errors
    assert serializer.validated_data == {"name": "a", "tags": ["x", "y"]}


def test_selected_backend_must_be_installed():
    from aiodrf import checks

    with override_settings(
        AIODRF={"ATOMIC_SAVE": "yes"}, FASTDRF={"SERIALIZER_BACKEND": "msgspec"}
    ):
        ids = [message.id for message in checks.check_settings(app_configs=None)]
    # msgspec is installed here; the type error is reported.
    assert ids == ["aiodrf.E006"]


# -- The output schema is the output contract -----------------------------------------


class MsgspecPublic(msgspec.Struct):
    name: str


class MsgspecInternal(MsgspecPublic):
    secret: str


class MsgspecOuter(msgspec.Struct):
    owner: MsgspecPublic
    members: list[MsgspecPublic] = []


class PydanticPublic(pydantic.BaseModel):
    name: str


class PydanticInternal(PydanticPublic):
    secret: str


class PydanticOuter(pydantic.BaseModel):
    owner: PydanticPublic
    members: list[PydanticPublic] = []


LIBRARIES = {
    "msgspec": (MsgspecSerializer, MsgspecPublic, MsgspecInternal, MsgspecOuter),
    "pydantic": (PydanticSerializer, PydanticPublic, PydanticInternal, PydanticOuter),
}


def internal(library):
    _, _, Internal, _ = LIBRARIES[library]
    return Internal(name="public", secret="sensitive")


@pytest.mark.parametrize("library", LIBRARIES)
@pytest.mark.parametrize("many", [False, True])
def test_fields_of_a_subclass_do_not_leak(library, many):
    base, Public, _, _ = LIBRARIES[library]
    serializer_class = type(
        "PublicSerializer", (base,), {"Meta": type("Meta", (), {"schema": Public})}
    )
    value = internal(library)
    data = serializer_class([value] if many else value, many=many).data
    assert data == ([{"name": "public"}] if many else {"name": "public"})


@pytest.mark.parametrize("library", LIBRARIES)
@pytest.mark.parametrize("many", [False, True])
def test_nested_fields_of_a_subclass_do_not_leak(library, many):
    base, _, _, Outer = LIBRARIES[library]
    serializer_class = type(
        "OuterSerializer", (base,), {"Meta": type("Meta", (), {"schema": Outer})}
    )
    value = Outer(owner=internal(library), members=[internal(library)])
    expected = {"owner": {"name": "public"}, "members": [{"name": "public"}]}
    data = serializer_class([value] if many else value, many=many).data
    assert data == ([expected] if many else expected)


@pytest.mark.parametrize("library", LIBRARIES)
@pytest.mark.parametrize("inherited", [False, True])
def test_a_list_keeps_the_childs_representation(library, inherited):
    base, _, Internal, _ = LIBRARIES[library]

    class Redacted(base):
        class Meta:
            schema = Internal

        def to_representation(self, instance):
            data = super().to_representation(instance)
            if not self.context.get("staff"):
                data.pop("secret")
            return data

    serializer_class = type("Child", (Redacted,), {}) if inherited else Redacted
    value = internal(library)
    assert serializer_class(value).data == {"name": "public"}
    assert serializer_class([value], many=True).data == [{"name": "public"}]
    staff = serializer_class([value], many=True, context={"staff": True}).data
    assert staff == [{"name": "public", "secret": "sensitive"}]


@pytest.mark.parametrize("library", LIBRARIES)
@pytest.mark.parametrize("warm", [False, True])
async def test_a_list_keeps_a_representation_assigned_to_its_child(
    library, warm, monkeypatch
):
    # Assigned on the instance, as DRF's attribute lookup allows: per request,
    # so the class does not show it.
    base, _, Internal, _ = LIBRARIES[library]
    serializer_class = type(
        "Internal", (base,), {"Meta": type("Meta", (), {"schema": Internal})}
    )
    values = [internal(library)]
    if warm:
        assert serializer_class(values, many=True).data == [
            {"name": "public", "secret": "sensitive"}
        ]
    backend = type(serializer_class().backend)
    monkeypatch.setattr(
        backend, "dump_many", lambda *args: pytest.fail("child skipped")
    )

    def redact(instance):
        return {"name": instance.name, "secret": "[redacted]"}

    serializer = serializer_class(values, many=True)
    serializer.child.to_representation = redact
    assert serializer.data == [redact(value) for value in values]
    serializer = serializer_class(values, many=True)
    serializer.child.to_representation = redact
    assert await serializer.adata() == [redact(value) for value in values]


@pytest.mark.parametrize("library", LIBRARIES)
async def test_a_list_awaits_the_childs_async_representation(library):
    base, _, Internal, _ = LIBRARIES[library]

    class Redacted(base):
        class Meta:
            schema = Internal

        async def ato_representation(self, instance):
            data = self.backend.dump(self.get_output_schema(), instance)
            data.pop("secret")
            return data

    value = internal(library)
    assert await Redacted(value).adata() == {"name": "public"}
    assert await Redacted([value], many=True).adata() == [{"name": "public"}]


@pytest.mark.parametrize("library", LIBRARIES)
async def test_plain_children_are_dumped_in_one_call(library, monkeypatch):
    base, Public, _, _ = LIBRARIES[library]
    serializer_class = type(
        "PublicSerializer", (base,), {"Meta": type("Meta", (), {"schema": Public})}
    )
    calls = []
    backend = type(serializer_class().backend)
    dump_many = backend.dump_many
    monkeypatch.setattr(
        backend,
        "dump_many",
        lambda self, *args: calls.append(args) or dump_many(self, *args),
    )
    values = [Public(name="a"), Public(name="b")]
    assert serializer_class(values, many=True).data == [{"name": "a"}, {"name": "b"}]
    assert await serializer_class(values, many=True).adata() == [
        {"name": "a"},
        {"name": "b"},
    ]
    assert len(calls) == 2


# -- The fields describe the output, for DRF's introspection (OrderingFilter) ---------


class PydanticAccount(pydantic.BaseModel):
    id: int
    full_name: str = pydantic.Field(serialization_alias="fullName")
    password: str = pydantic.Field(exclude=True)


class MsgspecAccount(msgspec.Struct, rename={"full_name": "fullName"}):
    id: int
    full_name: str


@pytest.mark.parametrize(
    ("base", "schema"),
    [(PydanticSerializer, PydanticAccount), (MsgspecSerializer, MsgspecAccount)],
)
def test_fields_are_the_output_named_on_the_wire_read_from_the_attribute(base, schema):
    from rest_framework.filters import OrderingFilter

    serializer_class = type(
        "Accounts", (base,), {"Meta": type("Meta", (), {"schema": schema})}
    )
    fields = serializer_class().fields
    assert list(fields) == ["id", "fullName"]
    assert fields["fullName"].source == "full_name"

    view = type("View", (), {"get_serializer_class": lambda self: serializer_class})()
    from tests.testapp.models import Author

    ordering = OrderingFilter().get_default_valid_fields(
        Author.objects.none(), view, {}
    )
    valid = {name for name, _ in ordering}
    assert valid == {"id", "full_name"}


class RenamedStruct(msgspec.Struct, rename="camel"):
    first_name: str
    page_count: int = 1


class AliasedModel(pydantic.BaseModel):
    model_config = pydantic.ConfigDict(alias_generator=to_camel)
    first_name: str
    page_count: int = 1


@pytest.mark.parametrize(
    ("base", "schema"),
    [(MsgspecSerializer, RenamedStruct), (PydanticSerializer, AliasedModel)],
)
async def test_the_data_of_validated_input_is_named_as_on_the_wire(base, schema):
    serializer_class = type(
        "Renamed", (base,), {"Meta": type("Meta", (), {"schema": schema})}
    )
    body = {"firstName": "Ada", "pageCount": 3}
    serializer = serializer_class(data=body)
    assert serializer.is_valid(), serializer.errors
    assert serializer.data == body
    serializer = serializer_class(data=body)
    assert await serializer.ais_valid()
    assert await serializer.adata() == body

    partial = serializer_class(data={"pageCount": 4}, partial=True)
    assert partial.is_valid(), partial.errors
    assert partial.data == {"pageCount": 4}


class Choices(pydantic.BaseModel):
    x: int | bool
    d: dict[int, int] = {}
    items: list[int | bool] = []


def test_pydantic_errors_are_keyed_by_the_inputs_fields_and_indexes():
    from aiodrf.contrib.typed import adapt

    serializer = adapt(Choices)(data={"x": "abc", "d": {"k": 1}, "items": [1, "abc"]})
    assert not serializer.is_valid()
    errors = serializer.errors
    assert set(errors) == {"x", "d", "items"}
    assert isinstance(errors["x"], list)
    assert all(isinstance(message, str) for message in errors["x"])
    assert set(errors["d"]) == {"k"}
    assert isinstance(errors["d"]["k"], list)
    item_errors = errors["items"][1]
    assert isinstance(item_errors, list)
    assert errors["items"] == list_errors({1: item_errors}, 2)


class Point(msgspec.Struct, array_like=True):
    x: int
    y: int = 0


class Item(pydantic.BaseModel):
    name: str


class Numbers(pydantic.RootModel[list[int]]):
    pass


class Items(pydantic.RootModel[list[Item]]):
    pass


@pytest.mark.parametrize("as_dict", [False, True])
@pytest.mark.parametrize(
    ("schema", "data", "errors"),
    [
        (Point, ["a"], {0: ["Expected `int`, got `str`"]}),
        (Point, [1, "a"], {1: ["Expected `int`, got `str`"]}),
        (
            Numbers,
            [1, "a"],
            {
                1: [
                    "Input should be a valid integer, unable to parse string as an integer"
                ]
            },
        ),
        (Items, [{"name": "a"}, {}], {1: {"name": ["This field is required."]}}),
    ],
)
def test_errors_of_an_array_schema_are_keyed_by_position(schema, data, errors, as_dict):
    # A serializer's errors are a dict on every DRF version, so the top
    # level of an array input keys its items as DRF's ListField does.
    from aiodrf.contrib.typed import adapt

    rest_framework = {
        **settings.REST_FRAMEWORK,
        "LIST_SERIALIZER_ERRORS_AS_DICT": as_dict,
    }
    with override_settings(REST_FRAMEWORK=rest_framework):
        serializer = adapt(schema)(data=data)
        assert not serializer.is_valid()
        assert serializer.errors == errors
