"""
The DRF functions aiodrf follows statement by statement, and where.

aiodrf's async paths repeat these functions' steps with the awaited ones in
place. A DRF release that changes one needs its counterpart read again; this
ledger names it. The digest is of the function's source as the parser sees
it, without its docstring, so that rewording one changes nothing. Digests are
recorded for each DRF version of the test matrix.

When a digest is unknown: read the new function, bring the counterpart in
line (or confirm it needs nothing), and add the digest under that version.
``tests/async_backend/test_mirrors.py`` keeps the native backend's ledger.
"""

import ast
import hashlib
import importlib
import inspect
import textwrap

import pytest

# DRF function: (aiodrf counterparts, {DRF version: digest}).
MIRRORS = {
    "rest_framework.views:APIView.dispatch": (
        ["aiodrf.views:APIView.dispatch"],
        {"3.16": "35774e9080c3", "3.17": "35774e9080c3", "3.18": "35774e9080c3"},
    ),
    "rest_framework.views:APIView.initial": (
        ["aiodrf.views:APIView.ainitial", "aiodrf.views:_RequestPlan.initial"],
        {"3.16": "8797530e03a5", "3.17": "8797530e03a5", "3.18": "8797530e03a5"},
    ),
    "rest_framework.views:APIView.initialize_request": (
        ["aiodrf.views:_RequestPlan.initialize_request"],
        {"3.16": "8543bf8d352d", "3.17": "8543bf8d352d", "3.18": "8543bf8d352d"},
    ),
    "rest_framework.views:APIView.finalize_response": (
        ["aiodrf.views:APIView._finalize_response"],
        {"3.16": "072c8911ba4c", "3.17": "072c8911ba4c", "3.18": "0ab5d33b8c08"},
    ),
    "rest_framework.views:APIView.handle_exception": (
        ["aiodrf.views:APIView.ahandle_exception"],
        {"3.16": "cec85a7996d4", "3.17": "cec85a7996d4", "3.18": "cec85a7996d4"},
    ),
    "rest_framework.views:APIView.check_permissions": (
        ["aiodrf.views:APIView.acheck_permissions"],
        {"3.16": "49a69363014b", "3.17": "49a69363014b", "3.18": "49a69363014b"},
    ),
    "rest_framework.views:APIView.check_object_permissions": (
        ["aiodrf.views:APIView.acheck_object_permissions"],
        {"3.16": "ed38214ee36f", "3.17": "ed38214ee36f", "3.18": "ed38214ee36f"},
    ),
    "rest_framework.views:APIView.check_throttles": (
        ["aiodrf.views:APIView.acheck_throttles"],
        {"3.16": "a5a989d57cc6", "3.17": "a5a989d57cc6", "3.18": "a5a989d57cc6"},
    ),
    "rest_framework.request:Request._authenticate": (
        [
            "aiodrf.request:Request._authenticate",
            "aiodrf.request:Request._aauthenticate",
        ],
        {"3.16": "b42c6179fa4d", "3.17": "b42c6179fa4d", "3.18": "b42c6179fa4d"},
    ),
    "rest_framework.serializers:BaseSerializer.save": (
        ["aiodrf.aio._save:default_save"],
        {"3.16": "f2957b829ea6", "3.17": "f2957b829ea6", "3.18": "f2957b829ea6"},
    ),
    "rest_framework.serializers:BaseSerializer.is_valid": (
        ["aiodrf.aio._validate:default_is_valid"],
        {"3.16": "1ceae5a8d5db", "3.17": "1ceae5a8d5db", "3.18": "1ceae5a8d5db"},
    ),
    "rest_framework.serializers:BaseSerializer.many_init": (
        ["aiodrf.serializers:BaseSerializer.many_init"],
        {"3.16": "e6926f2f603c", "3.17": "e6926f2f603c", "3.18": "e6926f2f603c"},
    ),
    "rest_framework.serializers:Serializer.run_validation": (
        ["aiodrf.aio._validate:default_run_validation"],
        {"3.16": "9a180711f0e3", "3.17": "9a180711f0e3", "3.18": "9a180711f0e3"},
    ),
    "rest_framework.serializers:Serializer.to_internal_value": (
        ["aiodrf.aio._validate:_ato_internal_value"],
        {"3.16": "b2c00ae6caee", "3.17": "b2c00ae6caee", "3.18": "b2c00ae6caee"},
    ),
    "rest_framework.serializers:Serializer.run_validators": (
        ["aiodrf.aio._validate:_arun_validators"],
        {"3.16": "dfcb3efced49", "3.17": "dfcb3efced49", "3.18": "dfcb3efced49"},
    ),
    "rest_framework.serializers:Serializer.to_representation": (
        ["aiodrf.aio._represent:default_to_representation"],
        {"3.16": "cb8425981b4e", "3.17": "cb8425981b4e", "3.18": "cb8425981b4e"},
    ),
    "rest_framework.serializers:ListSerializer.to_internal_value": (
        ["aiodrf.aio._validate:_alist_to_internal_value"],
        {"3.16": "d3bc0602c10b", "3.17": "d3bc0602c10b", "3.18": "a20e27e18745"},
    ),
    "rest_framework.serializers:ListSerializer.run_child_validation": (
        ["aiodrf.aio._validate:_arun_child_validation"],
        {"3.16": "9043ce8b60ea", "3.17": "9043ce8b60ea", "3.18": "9043ce8b60ea"},
    ),
    "rest_framework.serializers:ListSerializer.create": (
        ["aiodrf.aio._save:_acall_write"],
        {"3.16": "dd2228e978cf", "3.17": "dd2228e978cf", "3.18": "dd2228e978cf"},
    ),
    "rest_framework.serializers:ListSerializer.to_representation": (
        ["aiodrf.serializers:ListSerializer.to_representation"],
        {"3.16": "aff0b98be09e", "3.17": "aff0b98be09e", "3.18": "aff0b98be09e"},
    ),
    "rest_framework.fields:Field.run_validation": (
        ["aiodrf.aio._validate:_convert_value", "aiodrf.aio._validate:_arun_field"],
        {"3.16": "56feaf1c6377", "3.17": "56feaf1c6377", "3.18": "56feaf1c6377"},
    ),
    "rest_framework.fields:Field.run_validators": (
        ["aiodrf.aio._validate:_arun_field_validators"],
        {"3.16": "d481069d6a75", "3.17": "d481069d6a75", "3.18": "d481069d6a75"},
    ),
    "rest_framework.fields:Field.validate_empty_values": (
        ["aiodrf.aio._validate:_convert_value"],
        {"3.16": "660c2585cf85", "3.17": "660c2585cf85", "3.18": "660c2585cf85"},
    ),
    "rest_framework.fields:CharField.run_validation": (
        ["aiodrf.aio._validate:_convert_value"],
        {"3.16": "54b7a50a534d", "3.17": "54b7a50a534d", "3.18": "54b7a50a534d"},
    ),
    "rest_framework.relations:RelatedField.run_validation": (
        ["aiodrf.aio._validate:_convert_value"],
        {"3.16": "9509a380ec38", "3.17": "9509a380ec38", "3.18": "9509a380ec38"},
    ),
    "rest_framework.fields:ListField.to_internal_value": (
        ["aiodrf.aio._validate:_acollection"],
        {"3.16": "a7d25fd2f99a", "3.17": "a7d25fd2f99a", "3.18": "a7d25fd2f99a"},
    ),
    "rest_framework.fields:DictField.to_internal_value": (
        ["aiodrf.aio._validate:_acollection"],
        {"3.16": "5c526896b19a", "3.17": "5c526896b19a", "3.18": "ea71480603de"},
    ),
    "rest_framework.mixins:CreateModelMixin.create": (
        ["aiodrf.mixins:CreateModelMixin._create"],
        {"3.16": "7e84c664fbec", "3.17": "7e84c664fbec", "3.18": "7e84c664fbec"},
    ),
    "rest_framework.mixins:ListModelMixin.list": (
        ["aiodrf.mixins:ListModelMixin._list"],
        {"3.16": "53ddef70a7a8", "3.17": "53ddef70a7a8", "3.18": "53ddef70a7a8"},
    ),
    "rest_framework.mixins:RetrieveModelMixin.retrieve": (
        ["aiodrf.mixins:RetrieveModelMixin._retrieve"],
        {"3.16": "ff64a13929e9", "3.17": "ff64a13929e9", "3.18": "ff64a13929e9"},
    ),
    "rest_framework.mixins:UpdateModelMixin.update": (
        ["aiodrf.mixins:UpdateModelMixin._update"],
        {"3.16": "e5bdb3bc98fe", "3.17": "e5bdb3bc98fe", "3.18": "e5bdb3bc98fe"},
    ),
    "rest_framework.mixins:DestroyModelMixin.destroy": (
        ["aiodrf.mixins:DestroyModelMixin._destroy"],
        {"3.16": "b1f29d8fb444", "3.17": "b1f29d8fb444", "3.18": "b1f29d8fb444"},
    ),
    "rest_framework.generics:GenericAPIView.get_object": (
        ["aiodrf.generics:GenericAPIView.aget_object"],
        {"3.16": "dc63d7cad36c", "3.17": "dc63d7cad36c", "3.18": "dc63d7cad36c"},
    ),
    "rest_framework.permissions:DjangoModelPermissions.has_permission": (
        ["aiodrf.contrib.permissions:DjangoModelPermissions.ahas_permission"],
        {"3.16": "4a79e810d372", "3.17": "4a79e810d372", "3.18": "4a79e810d372"},
    ),
    "rest_framework.test:APIClient.logout": (
        ["aiodrf.test:AsyncAPIClient.logout", "aiodrf.test:AsyncAPIClient.alogout"],
        {"3.16": "71491b251971", "3.17": "71491b251971", "3.18": "71491b251971"},
    ),
}


def resolve(path):
    module, _, attributes = path.partition(":")
    found = importlib.import_module(module)
    for name in attributes.split("."):
        found = getattr(found, name)
    return found


def digest(function):
    tree = ast.parse(textwrap.dedent(inspect.getsource(function)))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and (
            ast.get_docstring(node, clean=False) is not None
        ):
            node.body = node.body[1:] or [ast.Pass()]
    # ``ast.unparse`` is the same text on every supported Python; ``ast.dump``
    # is not.
    return hashlib.sha256(ast.unparse(tree).encode()).hexdigest()[:12]


@pytest.mark.parametrize("upstream", sorted(MIRRORS))
def test_mirrored_drf_functions_are_known(upstream):
    counterparts, digests = MIRRORS[upstream]
    found = digest(resolve(upstream))
    assert found in digests.values(), (
        f"{upstream} changed ({found}); review {', '.join(counterparts)}"
    )


@pytest.mark.parametrize("upstream", sorted(MIRRORS))
def test_the_counterparts_exist(upstream):
    for counterpart in MIRRORS[upstream][0]:
        assert callable(resolve(counterpart)), counterpart
