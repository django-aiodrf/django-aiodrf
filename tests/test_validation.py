"""
The async validation walker against DRF.

A serializer with an async hook cannot use DRF's ``to_internal_value``, so
aiodrf walks the fields itself. Everything observable must equal what DRF
does for the same serializer with synchronous hooks: values, errors, error
codes and the *order* in which user code runs.
"""

import warnings

import pytest
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase
from django.utils.asyncio import async_unsafe
from rest_framework import serializers as drf_serializers

from aiodrf import aio, serializers
from aiodrf.aio import _classify
from aiodrf.compat import DRF_VERSION
from aiodrf.test import count_hops
from tests.testapp.models import Author


def traced(trace, name, *, asynchronous, transform=None):
    """A ``validate_<field>``-style hook that records when it runs."""

    def run(value):
        trace.append(name)
        return transform(value) if transform else value

    if asynchronous:

        async def hook(self, value):
            return run(value)

    else:

        def hook(self, value):
            return run(value)

    return hook


def ordered_serializers():
    """The same serializer three ways: DRF, aiodrf all-sync hooks, aiodrf mixed."""
    traces = {"drf": [], "mixed": []}

    def validator(trace, name):
        def validate(value):
            trace.append(name)

        return validate

    def build(base, key, async_names):
        trace = traces[key]
        return type(
            f"Ordered{key.title()}",
            (base.Serializer,),
            {
                "first": base.IntegerField(
                    validators=[validator(trace, "first.validator")]
                ),
                "second": base.IntegerField(
                    validators=[validator(trace, "second.validator")]
                ),
                "third": base.CharField(
                    validators=[validator(trace, "third.validator")]
                ),
                "validate_first": traced(
                    trace, "validate_first", asynchronous="first" in async_names
                ),
                "validate_second": traced(trace, "validate_second", asynchronous=False),
                "validate_third": traced(
                    trace, "validate_third", asynchronous="third" in async_names
                ),
                "validate": traced(
                    trace, "validate", asynchronous="all" in async_names
                ),
            },
        )

    return (
        traces,
        build(drf_serializers, "drf", ()),
        build(serializers, "mixed", ("first", "third", "all")),
    )


async def test_hooks_run_in_drf_order():
    traces, drf_class, mixed_class = ordered_serializers()
    data = {"first": 1, "second": 2, "third": "x"}
    assert drf_class(data=data).is_valid()
    mixed = mixed_class(data=data)
    assert await mixed.ais_valid(), mixed.errors
    assert traces["mixed"] == traces["drf"]
    assert traces["drf"][:3] == [
        "first.validator",
        "validate_first",
        "second.validator",
    ]


async def test_a_failing_field_stops_its_own_stages_only():
    traces, drf_class, mixed_class = ordered_serializers()
    data = {"first": "not a number", "second": 2, "third": ""}
    drf = drf_class(data=data)
    mixed = mixed_class(data=data)
    assert not drf.is_valid()
    assert not await mixed.ais_valid()
    assert mixed.errors == drf.errors
    assert list(mixed.errors) == list(drf.errors) == ["first", "third"]
    assert traces["mixed"] == traces["drf"] == ["second.validator", "validate_second"]


async def accepts(value):
    return None


async def rejects(value):
    raise serializers.ValidationError("Rejected.", code="rejected")


class BlankSerializer(serializers.Serializer):
    required = serializers.CharField(validators=[accepts])
    optional = serializers.CharField(
        allow_blank=True, min_length=3, validators=[accepts]
    )
    padded = serializers.CharField(validators=[accepts])


class BlankDRFSerializer(drf_serializers.Serializer):
    required = drf_serializers.CharField()
    optional = drf_serializers.CharField(allow_blank=True, min_length=3)
    padded = drf_serializers.CharField()


async def test_char_field_blank_handling_survives_async_validators():
    # ``CharField.run_validation`` handles blank input before validators run.
    data = {"required": "", "optional": "", "padded": "  x  "}
    drf = BlankDRFSerializer(data=data)
    mixed = BlankSerializer(data=data)
    assert not drf.is_valid()
    assert not await mixed.ais_valid()
    assert mixed.errors == drf.errors
    assert mixed.errors["required"][0].code == "blank"
    assert "optional" not in mixed.errors

    data["required"] = "ok"
    mixed = BlankSerializer(data=data)
    assert await mixed.ais_valid(), mixed.errors
    assert mixed.validated_data == {"required": "ok", "optional": "", "padded": "x"}


class RelationSerializer(serializers.Serializer):
    author = serializers.PrimaryKeyRelatedField(
        queryset=Author.objects.all(), validators=[accepts]
    )
    editor = serializers.PrimaryKeyRelatedField(
        queryset=Author.objects.all(), allow_null=True, validators=[accepts]
    )
    names = serializers.ListField(child=serializers.CharField())


class RelationTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.author = Author.objects.create(name="Ursula")

    async def test_relation_lookup_leaves_the_loop_when_validators_are_async(self):
        data = {"author": self.author.pk, "editor": "", "names": ["a"]}
        serializer = RelationSerializer(data=data)
        with count_hops() as hops:
            assert await serializer.ais_valid(), serializer.errors
        # ``''`` becomes ``None`` for relations, as in DRF.
        assert serializer.validated_data == {
            "author": self.author,
            "editor": None,
            "names": ["a"],
        }
        # One hop per lookup: the async validator of ``author`` runs between
        # them, and work is never moved across an async stage to save a hop.
        assert hops.count == 2, hops.calls

    async def test_relation_errors_are_drfs(self):
        serializer = RelationSerializer(
            data={"author": 999_999, "editor": None, "names": []}
        )
        assert not await serializer.ais_valid()
        assert serializer.errors["author"][0].code == "does_not_exist"

    async def test_callable_default_that_queries(self):
        def first_author():
            return Author.objects.first()

        class Defaulted(serializers.Serializer):
            author = serializers.HiddenField(default=first_author)
            note = serializers.CharField(validators=[accepts])

        serializer = Defaulted(data={"note": "x"})
        assert await serializer.ais_valid(), serializer.errors
        assert serializer.validated_data["author"] == self.author


def rejects_synchronously(value):
    raise serializers.ValidationError("Rejected.", code="rejected")


async def test_async_validators_report_like_drf():
    class Rejecting(serializers.Serializer):
        value = serializers.IntegerField(min_value=10, validators=[rejects])

    class RejectingDRF(drf_serializers.Serializer):
        value = drf_serializers.IntegerField(
            min_value=10, validators=[rejects_synchronously]
        )

    serializer = Rejecting(data={"value": 1})
    drf = RejectingDRF(data={"value": 1})
    assert not await serializer.ais_valid()
    assert not drf.is_valid()
    # DRF accumulates the errors of all validators of a field, in their order.
    codes = [error.code for error in serializer.errors["value"]]
    assert codes == [error.code for error in drf.errors["value"]]
    assert sorted(codes) == ["min_value", "rejected"]


async def test_instance_level_hooks_are_found_like_drf_finds_them():
    class Dynamic(serializers.Serializer):
        name = serializers.CharField(validators=[accepts])
        other = serializers.CharField()

        def __getattr__(self, attr):
            if attr == "validate_other":
                return lambda value: value.upper()
            raise AttributeError(attr)

    serializer = Dynamic(data={"name": "a", "other": "b"})
    assert await serializer.ais_valid(), serializer.errors
    assert serializer.validated_data == {"name": "a", "other": "B"}


async def test_an_async_hook_set_on_the_instance_is_awaited():
    # The class only has DRF's ``validate``; the instance's shadows it.
    class Plain(drf_serializers.Serializer):
        name = drf_serializers.CharField()

    async def validate(attrs):
        return {"name": attrs["name"].upper()}

    serializer = Plain(data={"name": "a"})
    serializer.validate = validate
    assert _classify.validation_kind(serializer) is _classify.Kind.ASYNC
    assert await aio.is_valid(serializer), serializer.errors
    assert serializer.validated_data == {"name": "A"}


def test_async_children_with_custom_collection_conversion_are_rejected():
    # DRF validates the children synchronously inside ``to_internal_value``;
    # a coroutine there would be dropped and the input accepted.
    class CustomList(serializers.ListField):
        pass

    class Collection(serializers.Serializer):
        names = CustomList(child=serializers.CharField(validators=[rejects]))

    with pytest.raises(ImproperlyConfigured, match="names"):
        _classify.validation_kind(Collection(data={}))


def test_async_children_under_an_instance_child_validation_are_rejected():
    # aiodrf shadows ``run_child_validation`` while it converts a collection;
    # one the project set on the instance is never shadowed, it is refused.
    class Collection(serializers.Serializer):
        names = serializers.ListField(child=serializers.CharField(validators=[rejects]))

    serializer = Collection(data={})
    serializer.fields["names"].run_child_validation = lambda value: value
    with pytest.raises(ImproperlyConfigured, match="names"):
        _classify.validation_kind(serializer)


async def test_nested_override_is_reached_from_the_parent():
    class Child(serializers.Serializer):
        value = serializers.IntegerField()

        async def arun_validation(self, data=serializers.empty):
            raise serializers.ValidationError("Child rejects everything.")

    class Parent(serializers.Serializer):
        child = Child()

    serializer = Parent(data={"child": {"value": 1}})
    assert not await serializer.ais_valid()
    assert "Child rejects everything." in str(serializer.errors["child"])


@pytest.mark.skipif(DRF_VERSION < (3, 18), reason="DRF 3.18 deprecated the list format")
async def test_the_list_error_format_is_deprecated_like_in_drf():
    from django.test import override_settings
    from rest_framework.deprecation import RemovedInDRF320Warning

    class Item(drf_serializers.Serializer):
        name = drf_serializers.CharField(validators=[accepts])

    class DRFItem(drf_serializers.Serializer):
        name = drf_serializers.CharField()

    data = [{"name": "a"}, {}]
    with override_settings(REST_FRAMEWORK={"LIST_SERIALIZER_ERRORS_AS_DICT": False}):
        reference = DRFItem(data=data, many=True)
        with pytest.warns(RemovedInDRF320Warning):
            assert not reference.is_valid()
        serializer = Item(data=data, many=True)
        assert _classify.validation_kind(serializer) is _classify.Kind.ASYNC
        with pytest.warns(RemovedInDRF320Warning):
            assert not await aio.is_valid(serializer)
    assert (
        serializer.errors
        == reference.errors
        == [{}, {"name": ["This field is required."]}]
    )


# -- Per-class classification --------------------------------------------------


class PlainSerializer(drf_serializers.Serializer):
    name = drf_serializers.CharField()
    count = drf_serializers.IntegerField(required=False)

    def validate_name(self, value):
        return value


class DynamicFieldsSerializer(PlainSerializer):
    def get_fields(self):
        fields = super().get_fields()
        if self.context.get("short"):
            fields.pop("count")
        return fields


def test_plain_serializers_are_classified_once_per_class():
    from unittest import mock

    _classify._CLASS_KINDS.clear()
    with mock.patch.object(
        _classify, "_validation_kind", wraps=_classify._validation_kind
    ) as walk:
        for _ in range(3):
            assert (
                _classify.validation_kind(PlainSerializer(data={"name": "a"}))
                is _classify.Kind.UNKNOWN
            )
    assert walk.call_count == 1
    with mock.patch.object(
        _classify, "_async_representation", wraps=_classify._async_representation
    ) as walk:
        for _ in range(3):
            assert (
                _classify.has_async_representation(PlainSerializer({"name": "a"}))
                is False
            )
    assert walk.call_count == 1


def test_a_serializer_class_is_looked_at_once_to_tell_it_is_declarative():
    from unittest import mock

    class Declarative(drf_serializers.Serializer):
        name = drf_serializers.CharField()

    with mock.patch.object(
        _classify.inspect, "isroutine", wraps=_classify.inspect.isroutine
    ) as look:
        assert _classify.is_declarative_class(Declarative)
        assert look.call_count
        look.reset_mock()
        assert _classify._is_declarative(Declarative(data={}))
        assert _classify.is_declarative_class(Declarative)
    assert look.call_count == 0


@pytest.mark.parametrize(
    "make",
    [
        pytest.param(
            lambda: DynamicFieldsSerializer(data={}), id="get_fields override"
        ),
        pytest.param(
            lambda: PlainSerializer(data={}, validators=[accepts]),
            id="constructor kwargs",
        ),
        pytest.param(lambda: PlainSerializer(data={}, many=True), id="many=True"),
    ],
)
def test_serializers_whose_fields_may_differ_per_instance_are_walked_each_time(make):
    from unittest import mock

    _classify._CLASS_KINDS.clear()
    with mock.patch.object(
        _classify, "_validation_kind", wraps=_classify._validation_kind
    ) as walk:
        for _ in range(2):
            _classify.validation_kind(make())
    assert walk.call_count >= 2


def test_a_hook_set_on_the_instance_is_not_taken_from_the_class():
    _classify._CLASS_KINDS.clear()
    assert _classify.validation_kind(PlainSerializer(data={})) is _classify.Kind.UNKNOWN

    serializer = PlainSerializer(data={})

    async def validate_name(value):
        return value

    serializer.validate_name = validate_name
    assert _classify.validation_kind(serializer) is _classify.Kind.ASYNC
    assert _classify.validation_kind(PlainSerializer(data={})) is _classify.Kind.UNKNOWN


def test_the_class_cache_follows_the_settings():
    from django.test import override_settings

    _classify._CLASS_KINDS.clear()
    assert _classify.validation_kind(PlainSerializer(data={})) is _classify.Kind.UNKNOWN
    with override_settings(AIODRF={"PURE_POLICIES": [PlainSerializer]}):
        assert (
            _classify.validation_kind(PlainSerializer(data={})) is _classify.Kind.PURE
        )
    assert _classify.validation_kind(PlainSerializer(data={})) is _classify.Kind.UNKNOWN


async def test_fields_edited_on_the_instance_are_not_taken_from_the_class():
    # Warm the class cache with an ordinary instance, then change the fields
    # of another one before its validation: DRF customization that the class
    # knows nothing about.
    calls = []

    async def reject(value):
        calls.append(value)
        raise drf_serializers.ValidationError("must reject")

    _classify._CLASS_KINDS.clear()
    assert await aio.is_valid(PlainSerializer(data={"name": "a"}))

    serializer = PlainSerializer(data={"name": "b"})
    serializer.fields["name"].validators.append(reject)
    with warnings.catch_warnings():
        warnings.simplefilter("error")  # "coroutine ... was never awaited"
        assert not await aio.is_valid(serializer)
    assert serializer.errors == {"name": ["must reject"]}
    assert calls == ["b"]

    serializer = PlainSerializer(data={"name": "c"})
    serializer.fields.pop("count")
    serializer.validators.append(reject)
    assert not await aio.is_valid(serializer)
    assert calls == ["b", {"name": "c"}]
    # And an untouched instance still gets the class's answer.
    assert await aio.is_valid(PlainSerializer(data={"name": "d"}))


class NestedChild(drf_serializers.Serializer):
    number = drf_serializers.IntegerField()

    async def get_number(self, instance):
        return instance["number"] + 1


class NestedParent(drf_serializers.Serializer):
    child = NestedChild()
    children = NestedChild(many=True, required=False)
    names = drf_serializers.ListField(child=drf_serializers.CharField(), required=False)


class Shouting(drf_serializers.CharField):
    pass


class ChildWithCustomField(drf_serializers.Serializer):
    value = Shouting()


class ParentOfCustomField(drf_serializers.Serializer):
    child = ChildWithCustomField()


class ParentOfDynamicChild(drf_serializers.Serializer):
    child = DynamicFieldsSerializer()


class ListOfCustomField(drf_serializers.Serializer):
    names = drf_serializers.ListField(child=Shouting())


def _walks(make):
    """How often the fields of ``make()``'s serializer were walked, per half."""
    from unittest import mock

    _classify._CLASS_KINDS.clear()
    with (
        mock.patch.object(
            _classify, "_validation_kind", wraps=_classify._validation_kind
        ) as validation,
        mock.patch.object(
            _classify, "_async_representation", wraps=_classify._async_representation
        ) as representation,
    ):
        kinds = [
            (
                _classify.validation_kind(make()),
                _classify.has_async_representation(make()),
            )
            for _ in range(3)
        ]
    cls = type(make())
    walked = [
        sum(type(call.args[0]) is cls for call in walk.call_args_list)
        for walk in (validation, representation)
    ]
    return kinds, walked


def test_nested_static_serializers_are_classified_once_per_class():
    kinds, walked = _walks(lambda: NestedParent({"child": {"number": 1}}))
    assert kinds == [(_classify.Kind.PURE, False)] * 3
    assert walked == [1, 1]


@pytest.mark.parametrize(
    "make",
    [
        pytest.param(
            lambda: ParentOfCustomField(data={}), id="custom field of a child"
        ),
        pytest.param(lambda: ParentOfDynamicChild(data={}), id="child with get_fields"),
        pytest.param(
            lambda: ListOfCustomField(data={}), id="custom child of a ListField"
        ),
    ],
)
def test_nested_serializers_whose_fields_may_differ_are_walked_each_time(make):
    _, walked = _walks(make)
    assert walked == [3, 3]


@pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")
async def test_a_nested_serializers_fields_edited_on_the_instance_are_respected():
    calls = []

    async def reject(value):
        calls.append(value)
        raise drf_serializers.ValidationError("must reject")

    _classify._CLASS_KINDS.clear()
    source = {"child": {"number": 1}}
    assert await aio.data(NestedParent(source)) == {"child": {"number": 1}}
    assert await aio.is_valid(NestedParent(data=source))

    serializer = NestedParent(source)
    serializer.fields["child"].fields["number"] = (
        drf_serializers.SerializerMethodField()
    )
    assert await aio.data(serializer) == {"child": {"number": 2}}

    serializer = NestedParent(data={"child": {"number": 3}})
    serializer.fields["child"].fields["number"].validators.append(reject)
    assert not await aio.is_valid(serializer)
    assert serializer.errors == {"child": {"number": ["must reject"]}}
    assert calls == [3]

    # Untouched instances still get the class's answers.
    assert await aio.data(NestedParent(source)) == {"child": {"number": 1}}
    assert await aio.is_valid(NestedParent(data={"child": {"number": 4}}))
    assert calls == [3]


@pytest.mark.parametrize("many", [False, True])
@pytest.mark.parametrize("order", [(False, True, False), (True, False, True)])
async def test_nested_dynamic_validators_are_not_class_facts(many, order):
    calls = []

    async def reject(value):
        calls.append(value)
        raise drf_serializers.ValidationError("must reject")

    class Child(drf_serializers.Serializer):
        value = drf_serializers.IntegerField()

        @async_unsafe("get_fields must run in the worker")
        def get_fields(self):
            result = super().get_fields()
            if self.context["reject"]:
                result["value"].validators.append(reject)
            return result

    class Parent(drf_serializers.Serializer):
        child = Child(many=many)

    for reject_input in order:
        child = {"value": 2}
        serializer = Parent(
            data={"child": [child] if many else child}, context={"reject": reject_input}
        )
        assert await aio.is_valid(serializer) is not reject_input
    assert calls == [2] * sum(order)


@pytest.mark.parametrize(
    "collection", [drf_serializers.ListField, drf_serializers.DictField]
)
@pytest.mark.parametrize(
    "values", [["1", "2", "bad"], ["1", None, "3"], [], None, "invalid"]
)
async def test_collection_children_preserve_drf_values_errors_and_order(
    collection, values
):
    traces = {"drf": [], "async": []}

    def build(key):
        def validate(value):
            traces[key].append(("child", value))
            if value == 2:
                raise drf_serializers.ValidationError("two", code="two")

        async def awaited(value):
            validate(value)

        child = drf_serializers.IntegerField(
            allow_null=True, validators=[awaited if key == "async" else validate]
        )

        class Example(drf_serializers.Serializer):
            items = collection(child=child, allow_empty=False, allow_null=True)

            def validate_items(self, value):
                traces[key].append(("parent", value))
                return value

        return Example

    if collection is drf_serializers.DictField and isinstance(values, list):
        values = {str(index): value for index, value in enumerate(values)}
    reference = build("drf")(data={"items": values})
    candidate = build("async")(data={"items": values})
    assert await aio.is_valid(candidate) == reference.is_valid()
    assert candidate.errors == reference.errors
    assert candidate.validated_data == reference.validated_data
    assert traces["async"] == traces["drf"]


async def test_nested_async_collections_and_partial_defaults():
    calls = []

    async def checked(value):
        calls.append(value)

    @async_unsafe("default on loop")
    def default():
        calls.append("default")
        return {"raw": ["unvalidated default"]}

    class Example(drf_serializers.Serializer):
        values = drf_serializers.DictField(
            child=drf_serializers.ListField(
                child=drf_serializers.IntegerField(validators=[checked])
            ),
            default=default,
        )

    serializer = Example(data={"values": {"a": ["1", "2"]}})
    assert await aio.is_valid(serializer)
    assert serializer.validated_data == {"values": {"a": [1, 2]}}
    assert calls == [1, 2]
    calls.clear()
    serializer = Example(data={})
    assert await aio.is_valid(serializer)
    assert serializer.validated_data == {"values": {"raw": ["unvalidated default"]}}
    assert calls == ["default"]
    calls.clear()
    serializer = Example(data={}, partial=True)
    assert await aio.is_valid(serializer)
    assert serializer.validated_data == {}
    assert calls == []


@pytest.mark.aiodrf_settings(SERIALIZER_BACKEND_FALLBACK="drf")
async def test_nested_representation_follows_each_instances_fields():
    class Child(drf_serializers.Serializer):
        value = drf_serializers.IntegerField()

        def get_fields(self):
            result = super().get_fields()
            if self.context["async"]:
                result["value"] = drf_serializers.SerializerMethodField()
            return result

        async def get_value(self, instance):
            return instance["value"] + 1

    class Parent(drf_serializers.Serializer):
        child = Child()

    for asynchronous in (False, True, False, True):
        serializer = Parent({"child": {"value": 1}}, context={"async": asynchronous})
        assert await aio.data(serializer) == {
            "child": {"value": 2 if asynchronous else 1}
        }


async def test_dynamic_fields_inside_nested_collections_are_built_in_the_worker():
    calls = []

    async def checked(value):
        calls.append(value)

    class Child(drf_serializers.Serializer):
        @async_unsafe("nested get_fields on loop")
        def get_fields(self):
            return {"value": drf_serializers.IntegerField(validators=[checked])}

    class Parent(drf_serializers.Serializer):
        items = drf_serializers.DictField(
            child=drf_serializers.ListField(child=Child())
        )

    serializer = Parent(data={"items": {"a": [{"value": "1"}, {"value": "2"}]}})
    assert await aio.is_valid(serializer)
    assert serializer.validated_data == {"items": {"a": [{"value": 1}, {"value": 2}]}}
    assert calls == [1, 2]
