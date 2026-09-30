"""
The rewrite rules of ``python -m aiodrf.codemod``.

DRF imports
    ``from rest_framework.<module> import <names>`` imports each name aiodrf
    provides from ``aiodrf.<module>`` and keeps the rest on DRF.
    ``from rest_framework import <module>`` becomes ``from aiodrf import
    <module>`` when every ``<module>.<attr>`` the file uses exists in aiodrf.

adrf imports
    ``adrf.<module>`` imports move to the matching aiodrf module; names aiodrf
    spells differently are imported under adrf's name (``AAND`` is DRF's
    ``AND``, which aiodrf evaluates with async operands). adrf helpers
    without a counterpart, and modules aiodrf does not mirror, stay on adrf
    with a note.

adrf method names
    adrf renames view actions (``alist``, ``acreate``, ...); aiodrf keeps
    DRF's names. Methods of classes that look like views (their bases end
    in ``View``, ``ViewSet`` or ``Mixin``) are renamed when their first
    argument after ``self`` is ``request`` (or, as adrf declares them,
    ``*args, **kwargs``). ``self.<name>`` and ``super().<name>`` references
    in those classes follow: a hook adrf alone names, a method the class
    renamed, or any adrf name when the class derives from adrf's views;
    others are noted. So are keyword arguments of ``extend_schema_view()``.
    Serializer ``acreate``/``aupdate`` keep their names: aiodrf uses them
    with the same meaning (``super().acreate()`` is noted: aiodrf's
    serializers do not define it). A string literal naming
    an adrf action in an ``as_view({...})`` method map (the first argument
    or ``actions=``) becomes DRF's name.
"""

from collections.abc import Sequence
from typing import Any, cast

try:
    import libcst as cst
except ImportError as exc:  # pragma: no cover - depends on the environment
    from aiodrf.codemod import INSTALL

    raise ImportError(INSTALL) from exc

from aiodrf.contrib.adrf_compat._names import takes_request

__all__ = ["EXPORTS", "AioDRFTransformer", "Result", "transform_source"]

#: Names each aiodrf module provides for code importing ``rest_framework.<module>``.
#: ``tests/test_codemod.py`` checks this table against the modules.
EXPORTS = {
    "views": {"APIView", "exception_handler", "get_view_description", "get_view_name"},
    "generics": {
        "CreateAPIView",
        "DestroyAPIView",
        "GenericAPIView",
        "ListAPIView",
        "ListCreateAPIView",
        "RetrieveAPIView",
        "RetrieveDestroyAPIView",
        "RetrieveUpdateAPIView",
        "RetrieveUpdateDestroyAPIView",
        "UpdateAPIView",
        "get_object_or_404",
    },
    "mixins": {
        "CreateModelMixin",
        "DestroyModelMixin",
        "ListModelMixin",
        "RetrieveModelMixin",
        "UpdateModelMixin",
    },
    "viewsets": {
        "GenericViewSet",
        "ModelViewSet",
        "ReadOnlyModelViewSet",
        "ViewSet",
        "ViewSetMixin",
    },
    # aiodrf.serializers mirrors all of rest_framework.serializers.
    "serializers": None,
    "decorators": {
        "action",
        "api_view",
        "authentication_classes",
        "content_negotiation_class",
        "metadata_class",
        "parser_classes",
        "permission_classes",
        "renderer_classes",
        "schema",
        "throttle_classes",
        "versioning_class",
    },
    "response": {"Response"},
    "permissions": {
        "AND",
        "AllowAny",
        "BasePermission",
        "DjangoModelPermissions",
        "DjangoModelPermissionsOrAnonReadOnly",
        "DjangoObjectPermissions",
        "IsAdminUser",
        "IsAuthenticated",
        "IsAuthenticatedOrReadOnly",
        "NOT",
        "OR",
        "SAFE_METHODS",
    },
    "authentication": {
        "BaseAuthentication",
        "BasicAuthentication",
        "RemoteUserAuthentication",
        "SessionAuthentication",
        "TokenAuthentication",
        "get_authorization_header",
    },
    "throttling": {
        "AnonRateThrottle",
        "BaseThrottle",
        "ScopedRateThrottle",
        "SimpleRateThrottle",
        "UserRateThrottle",
    },
    "pagination": {
        "BasePagination",
        "CursorPagination",
        "LimitOffsetPagination",
        "PageNumberPagination",
    },
    "filters": {"BaseFilterBackend", "OrderingFilter", "SearchFilter"},
    "routers": {
        "APIRootView",
        "BaseRouter",
        "DefaultRouter",
        "DynamicRoute",
        "Route",
        "SimpleRouter",
    },
    "test": {
        "APIClient",
        "APIRequestFactory",
        "APITestCase",
        "APITransactionTestCase",
        "force_authenticate",
    },
}

# adrf module -> aiodrf module, and adrf names that changed.
ADRF_MODULES = {
    "adrf.views": "aiodrf.views",
    "adrf.generics": "aiodrf.generics",
    "adrf.mixins": "aiodrf.mixins",
    "adrf.viewsets": "aiodrf.viewsets",
    "adrf.serializers": "aiodrf.serializers",
    "adrf.decorators": "aiodrf.decorators",
    "adrf.permissions": "aiodrf.permissions",
    "adrf.routers": "aiodrf.routers",
    "adrf.test": "aiodrf.test",
    "adrf.requests": "aiodrf.request",
    "adrf.shortcuts": "django.shortcuts",
}
# adrf names aiodrf spells differently: (module, name) -> (module, name).
ADRF_NAMES = {
    ("adrf.requests", "AsyncRequest"): ("aiodrf.request", "Request"),
    ("adrf.views", "AsyncRequest"): ("aiodrf.request", "Request"),
    # aiodrf evaluates DRF's operators with async operands itself.
    ("adrf.permissions", "AAND"): ("aiodrf.permissions", "AND"),
    ("adrf.permissions", "AOR"): ("aiodrf.permissions", "OR"),
    ("adrf.permissions", "ANOT"): ("aiodrf.permissions", "NOT"),
    ("adrf.permissions", "AsyncBasePermission"): (
        "aiodrf.permissions",
        "BasePermission",
    ),
    ("adrf.permissions", "AsyncBasePermissionMetaClass"): (
        "rest_framework.permissions",
        "BasePermissionMetaclass",
    ),
    ("adrf.permissions", "AsyncOperandHolder"): (
        "rest_framework.permissions",
        "OperandHolder",
    ),
    ("adrf.permissions", "AsyncSingleOperandHolder"): (
        "rest_framework.permissions",
        "SingleOperandHolder",
    ),
}
# adrf names without an aiodrf counterpart: the import stays, with advice.
_OPERATOR_HELPER = "aiodrf evaluates DRF's `&`, `|`, `~` with async operands; drop it"
ADRF_KEPT = {
    ("adrf.mixins", "get_data"): "use `await aio.data(serializer)` (aiodrf.aio)",
    ("adrf.generics", "aget_object_or_404"): (
        "use `await self.aget_object()`; django.shortcuts.aget_object_or_404 does "
        "not turn a lookup value of the wrong type into a 404"
    ),
    ("adrf.viewsets", "getmembers"): "adrf's internal helper; use inspect.getmembers",
    ("adrf.views", "try_convert_operator"): _OPERATOR_HELPER,
    **{
        ("adrf.permissions", name): _OPERATOR_HELPER
        for name in (
            "AsyncLogicOperatorMixin",
            "AsyncOperandHolderMixin",
            "AsyncSingleLogicOperatorMixin",
            "is_async_perm_operator",
            "is_perm_operator",
            "try_convert_operator",
        )
    },
}

ADRF_ACTIONS = {
    "alist": "list",
    "acreate": "create",
    "aretrieve": "retrieve",
    "aupdate": "update",
    "partial_aupdate": "partial_update",
    "adestroy": "destroy",
}
ADRF_METHODS = {
    **ADRF_ACTIONS,
    "perform_acreate": "aperform_create",
    "perform_aupdate": "aperform_update",
    "perform_adestroy": "aperform_destroy",
    "get_apaginated_response": "aget_paginated_response",
    "check_async_permissions": "acheck_permissions",
    "check_async_object_permissions": "acheck_object_permissions",
    "check_async_throttles": "acheck_throttles",
}
# Renamed regardless of their signature (they do not take ``request``).
_ALWAYS_RENAMED = {
    "perform_acreate",
    "perform_aupdate",
    "perform_adestroy",
    "get_apaginated_response",
    "check_async_object_permissions",
}


class Result:
    __slots__ = ("code", "notes")

    def __init__(self, code: str, notes: list[str]) -> None:
        self.code = code
        self.notes = notes


def transform_source(source: str) -> Result:
    module = cst.parse_module(source)
    used = _module_attributes(module)
    transformer = AioDRFTransformer(used, _imported_names(module))
    return Result(module.visit(transformer).code, transformer.notes)


def _dotted(node: cst.BaseExpression) -> str | None:
    if isinstance(node, cst.Name):
        return node.value
    if isinstance(node, cst.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr.value}" if base else None
    return None


#: In ``_module_attributes``: the name is used other than as ``name.attr``
#: (assigned, passed, returned), so what it is used for is unknown.
ESCAPED = "<escaped>"


def _module_attributes(module: cst.Module) -> dict[str, set[str]]:
    """
    Map ``name`` to the attributes the file reads from it (``name.attr``),
    and to ``ESCAPED`` when the name is used any other way.
    """
    used: dict[str, set[str]] = {}

    class Collector(cst.CSTVisitor):
        def visit_Import(self, node: cst.Import) -> bool:
            return False

        def visit_ImportFrom(self, node: cst.ImportFrom) -> bool:
            return False

        def visit_Attribute(self, node: cst.Attribute) -> bool:
            if isinstance(node.value, cst.Name):
                used.setdefault(node.value.value, set()).add(node.attr.value)
                return False  # ``node.attr`` is an attribute, not a use of a name
            return True

        def visit_Name(self, node: cst.Name) -> None:
            used.setdefault(node.value, set()).add(ESCAPED)

    module.visit(Collector())
    return used


def _provides(module: str, name: str) -> bool:
    names = EXPORTS.get(module)
    return names is None or name in names


class AioDRFTransformer(cst.CSTTransformer):
    """Rewrite imports and hooks while preserving LibCST visitor signatures.

    Leave callbacks receive both original and updated nodes even when only
    the updated node is needed; these are framework callbacks, not dead APIs.
    """

    def __init__(
        self, used: dict[str, set[str]], imported: dict[str, str] | None = None
    ) -> None:
        super().__init__()
        self.used = used
        self.notes: list[str] = []
        # Local name -> dotted origin, for bases imported under another name.
        self.imported = imported or {}
        # One entry per enclosing class, for *that* class: a serializer nested
        # in a view is not a view, and its ``acreate`` must keep its name.
        self._classes: list[_Scope] = []
        self._noted: set[tuple[_Scope, str]] = set()
        # One entry per enclosing function: its name, and is it renamed?
        self._functions: list[tuple[str, bool]] = []
        self._class_depths: list[int] = []

    @property
    def _in_view(self) -> bool:
        return bool(self._classes) and self._classes[-1].view

    # -- Imports -------------------------------------------------------------

    def leave_SimpleStatementLine(
        self, original: cst.SimpleStatementLine, updated: cst.SimpleStatementLine
    ) -> cst.BaseStatement | cst.FlattenSentinel[cst.BaseStatement]:
        if len(updated.body) != 1 or not isinstance(updated.body[0], cst.ImportFrom):
            return self._rewrite_small_statements(updated)
        imports = self._rewrite_import(updated.body[0])
        if len(imports) == 1:
            return updated.with_changes(body=imports)
        # One line per module, the first keeping the original comments.
        lines = [updated.with_changes(body=[imports[0]])]
        lines += [cst.SimpleStatementLine(body=[node]) for node in imports[1:]]
        return cst.FlattenSentinel(lines)

    def leave_SimpleStatementSuite(
        self, original: cst.SimpleStatementSuite, updated: cst.SimpleStatementSuite
    ) -> cst.BaseSuite:
        # ``if flag: from adrf.views import APIView``
        return self._rewrite_small_statements(updated)

    def _rewrite_small_statements[
        S: (cst.SimpleStatementLine, cst.SimpleStatementSuite)
    ](self, statements: S) -> S:
        """Imports among statements separated by semicolons stay on their line."""
        body: list[cst.BaseSmallStatement] = []
        for node in statements.body:
            if isinstance(node, cst.ImportFrom):
                body.extend(self._rewrite_import(node))
            else:
                body.append(node)
        return statements.with_changes(body=body)

    def _rewrite_import(self, node: cst.ImportFrom) -> Sequence[cst.BaseSmallStatement]:
        if (
            node.relative
            or node.module is None
            or isinstance(node.names, cst.ImportStar)
        ):
            return [node]
        module = cast(str, _dotted(node.module))
        if module in ("rest_framework", "adrf"):
            return self._framework_modules(node, module)
        if module.startswith("rest_framework."):
            return self._rest_framework_names(
                node, module.removeprefix("rest_framework.")
            )
        if module in ADRF_MODULES:
            return self._adrf_names(node, module)
        if module.startswith("adrf."):
            self.notes.append(
                f"kept `from {module} import ...`: aiodrf has no {module} module"
            )
        return [node]

    def _framework_modules(self, node: Any, origin: str) -> list[cst.ImportFrom]:
        moved, kept = [], []
        for alias in node.names:
            name = alias.name.value
            local = alias.asname.name.value if alias.asname else name
            if name in EXPORTS and all(
                _provides(name, attr) for attr in self.used.get(local, ())
            ):
                moved.append(alias)
            else:
                if name in EXPORTS and ESCAPED in self.used.get(local, ()):
                    self.notes.append(
                        f"kept `from {origin} import {name}`: `{local}` is used other "
                        "than through its attributes"
                    )
                elif name in EXPORTS:
                    missing = sorted(
                        a for a in self.used.get(local, ()) if not _provides(name, a)
                    )
                    self.notes.append(
                        f"kept `from {origin} import {name}`: aiodrf.{name} lacks {missing}"
                    )
                elif origin == "adrf":
                    self.notes.append(
                        f"kept `from adrf import {name}`: aiodrf has no such module"
                    )
                kept.append(alias)
        return self._split(node, "aiodrf", moved, origin, kept)

    def _rest_framework_names(self, node: Any, module: str) -> list[cst.ImportFrom]:
        if module not in EXPORTS:
            return [node]
        moved = [alias for alias in node.names if _provides(module, alias.name.value)]
        kept = [
            alias for alias in node.names if not _provides(module, alias.name.value)
        ]
        return self._split(
            node, f"aiodrf.{module}", moved, f"rest_framework.{module}", kept
        )

    def _adrf_names(self, node: Any, module: str) -> list[cst.ImportFrom]:
        """One import per target module, in order; adrf keeps what aiodrf lacks."""
        groups: dict[str, list] = {}
        for alias in node.names:
            name = alias.name.value
            advice = ADRF_KEPT.get((module, name))
            if advice is not None:
                self.notes.append(f"kept `from {module} import {name}`: {advice}")
                groups.setdefault(module, []).append(alias)
                continue
            target, new_name = ADRF_NAMES.get(
                (module, name), (ADRF_MODULES[module], name)
            )
            renamed = alias
            if new_name != name:
                asname = alias.asname or cst.AsName(name=cst.Name(name))
                renamed = alias.with_changes(name=cst.Name(new_name), asname=asname)
            groups.setdefault(target, []).append(renamed)
        return [
            node.with_changes(
                module=cst.parse_expression(target), names=_fix_commas(aliases, node)
            )
            for target, aliases in groups.items()
        ]

    def _split(
        self,
        node: Any,
        new_module: str,
        moved: list[cst.ImportAlias],
        old_module: str,
        kept: list[cst.ImportAlias],
    ) -> list[cst.ImportFrom]:
        if not moved:
            return [node]
        new = node.with_changes(
            module=cst.parse_expression(new_module), names=_fix_commas(moved, node)
        )
        if not kept:
            return [new]
        old = node.with_changes(
            module=cst.parse_expression(old_module), names=_fix_commas(kept, node)
        )
        return [old, new]

    # -- adrf method names ------------------------------------------------------

    def visit_ClassDef(self, node: cst.ClassDef) -> None:
        verdict = _classify(node, self.imported)
        if verdict is None and _defines_adrf_methods(node):
            self.notes.append(
                f"left `class {node.name.value}` alone: it has adrf-style methods but "
                "its bases do not say whether it is a view"
            )
        self._classes.append(_Scope(node, verdict, self.imported))
        # A method is a function directly in the class body.
        self._class_depths.append(len(self._functions))

    def leave_ClassDef(
        self, original: cst.ClassDef, updated: cst.ClassDef
    ) -> cst.ClassDef:
        self._classes.pop()
        self._class_depths.pop()
        return updated

    def visit_FunctionDef(self, node: cst.FunctionDef) -> None:
        method = bool(self._class_depths) and self._class_depths[-1] == len(
            self._functions
        )
        self._functions.append(
            (node.name.value, method and self._in_view and _is_action(node))
        )

    def leave_FunctionDef(
        self, original: cst.FunctionDef, updated: cst.FunctionDef
    ) -> cst.FunctionDef:
        _, renamed = self._functions.pop()
        name = updated.name.value
        if renamed and name in ADRF_METHODS:
            return updated.with_changes(name=cst.Name(ADRF_METHODS[name]))
        return updated

    def leave_Attribute(
        self, original: cst.Attribute, updated: cst.Attribute
    ) -> cst.Attribute:
        name = updated.attr.value
        if (
            not self._classes
            or name not in ADRF_METHODS
            or not isinstance(updated.value, (cst.Name, cst.Call))
            or not _is_self_or_super(updated.value)
        ):
            return updated
        scope = self._classes[-1]
        if not scope.view:
            if scope.serializer and name in ("acreate", "aupdate"):
                self._note_once(
                    scope,
                    name,
                    f"`super().{name}()` in `class {scope.name}`: aiodrf's serializers "
                    f"do not define {name}(); run DRF's `ModelSerializer."
                    f"{ADRF_METHODS[name]}(self, ...)` with `sync_to_async`",
                )
            return updated
        # ``self.alist(...)`` / ``super().alist(...)``: in an action being
        # renamed the call follows the method it is in, and ``super()`` in a
        # method of that name follows it either way; elsewhere, the class's
        # own or inherited adrf method has aiodrf's name.
        function, renamed = self._functions[-1] if self._functions else (None, False)
        if renamed:
            return updated.with_changes(attr=cst.Name(ADRF_METHODS[name]))
        if function == name and isinstance(updated.value, cst.Call):
            return updated
        if name in scope.renames:
            return updated.with_changes(attr=cst.Name(ADRF_METHODS[name]))
        self._note_once(
            scope,
            name,
            f"left `{name}` in `class {scope.name}`: whether it is adrf's "
            f"method (aiodrf's `{ADRF_METHODS[name]}`) is not clear from its bases",
        )
        return updated

    def _note_once(self, scope: "_Scope", name: str, note: str) -> None:
        if (scope, name) not in self._noted:
            self._noted.add((scope, name))
            self.notes.append(note)

    def leave_Call(self, original: cst.Call, updated: cst.Call) -> cst.Call:
        if (
            isinstance(updated.func, cst.Attribute)
            and updated.func.attr.value == "as_view"
        ):
            return _rename_action_map(updated)
        if _dotted(updated.func) not in (
            "extend_schema_view",
            "drf_spectacular.utils.extend_schema_view",
        ):
            return updated
        args = [
            arg.with_changes(keyword=cst.Name(ADRF_METHODS[arg.keyword.value]))
            if arg.keyword is not None and arg.keyword.value in ADRF_METHODS
            else arg
            for arg in updated.args
        ]
        return updated.with_changes(args=args)


def _rename_action_map(call: cst.Call) -> cst.Call:
    """``as_view({"get": "alist"})``: the first argument or ``actions=``."""
    args = list(call.args)
    for index, arg in enumerate(args):
        keyword = arg.keyword.value if arg.keyword else None
        if arg.star or keyword not in (None, "actions") or (keyword is None and index):
            continue
        if isinstance(arg.value, cst.Dict):
            elements = [_rename_action(element) for element in arg.value.elements]
            args[index] = arg.with_changes(
                value=arg.value.with_changes(elements=elements)
            )
    return call.with_changes(args=args)


def _rename_action(element: cst.BaseDictElement) -> cst.BaseDictElement:
    value = element.value if isinstance(element, cst.DictElement) else None
    # A plain string literal only: a name or an f-string is not the codemod's to judge.
    if not isinstance(value, cst.SimpleString):
        return element
    name = value.evaluated_value
    new = ADRF_ACTIONS.get(name) if isinstance(name, str) else None
    if new is None:
        return element
    quoted = f"{value.prefix}{value.quote}{new}{value.quote}"
    return element.with_changes(value=value.with_changes(value=quoted))


class _Scope:
    """What the rewrite knows about one class it is inside."""

    __slots__ = ("name", "renames", "serializer", "view")

    def __init__(
        self, node: cst.ClassDef, verdict: bool | None, imported: dict[str, str]
    ) -> None:
        self.name = node.name.value
        self.view = bool(verdict)
        self.serializer = verdict is False
        self.renames = set()
        if self.view:
            # References that follow a rename: hooks only adrf names, the
            # class's own renamed methods, and adrf's inherited ones.
            self.renames = _ALWAYS_RENAMED | {
                statement.name.value
                for statement in node.body.body
                if isinstance(statement, cst.FunctionDef) and _is_action(statement)
            }
            if _derives_from_adrf(node, imported):
                self.renames |= ADRF_METHODS.keys()


_VIEW_SUFFIXES = ("View", "ViewSet", "APIView")
_FRAMEWORKS = ("adrf", "rest_framework", "aiodrf")


def _classify(node: cst.ClassDef, imported: dict[str, str]) -> bool | None:
    """
    True for a view, False for something else, None when the bases say
    nothing: a project's ``AuditMixin`` may be mixed into anything, so a
    ``Mixin`` counts only when it comes from adrf, DRF or aiodrf.
    """
    verdict = None
    for base in node.bases:
        name = _dotted(base.value) or ""
        head, _, rest = name.partition(".")
        # ``from adrf.viewsets import ModelViewSet as Base``: judge the origin.
        origin = imported.get(head)
        if origin is not None:
            name = f"{origin}.{rest}" if rest else origin
        last = name.rsplit(".", 1)[-1]
        framework = name.partition(".")[0] in _FRAMEWORKS
        if last.endswith(("Serializer", "Field")):
            return False
        if last.endswith(_VIEW_SUFFIXES) or (framework and last.endswith("Mixin")):
            verdict = True
    return verdict


def _derives_from_adrf(node: cst.ClassDef, imported: dict[str, str]) -> bool:
    for base in node.bases:
        name = _dotted(base.value) or ""
        head = name.partition(".")[0]
        origin = imported.get(head, head)
        if origin == "adrf" or origin.startswith("adrf."):
            return True
    return False


def _defines_adrf_methods(node: cst.ClassDef) -> bool:
    return any(
        isinstance(statement, cst.FunctionDef) and statement.name.value in ADRF_METHODS
        for statement in node.body.body
    )


def _is_action(node: cst.FunctionDef) -> bool:
    name = node.name.value
    return name in ADRF_METHODS and (
        name in _ALWAYS_RENAMED or takes_request(_parameters(node))
    )


def _imported_names(module: cst.Module) -> dict[str, str]:
    """Map the local names a file imports to where they come from."""
    imported = {}

    class Collector(cst.CSTVisitor):
        def visit_ImportFrom(self, node: Any) -> None:
            if node.module is None or isinstance(node.names, cst.ImportStar):
                return
            origin = _dotted(node.module)
            for alias in node.names:
                local = alias.asname.name.value if alias.asname else alias.name.value
                imported[local] = f"{origin}.{alias.name.value}"

    module.visit(Collector())
    return imported


def _parameters(function: cst.FunctionDef) -> list[str]:
    params = function.params
    names = [p.name.value for p in (*params.posonly_params, *params.params)][1:]
    if isinstance(params.star_arg, cst.Param):
        names.append("*" + params.star_arg.name.value)
    names += [p.name.value for p in params.kwonly_params]
    if params.star_kwarg is not None:
        names.append("**" + params.star_kwarg.name.value)
    return names


def _is_self_or_super(node: cst.BaseExpression) -> bool:
    if isinstance(node, cst.Name):
        return node.value == "self"
    return isinstance(node, cst.Call) and _dotted(node.func) == "super"


def _fix_commas(aliases: Sequence[Any], node: Any) -> list[cst.ImportAlias]:
    """
    A comma-separated alias list from ``aliases`` of the import ``node``.

    A multi-line list that ended with a comma keeps it, and every alias its
    comment: the comma after an alias holds the comment and the line break,
    and the last one also the line the closing parenthesis is on. Otherwise
    the list has no trailing comma.
    """
    last = node.names[-1].comma
    closing = last.whitespace_after if isinstance(last, cst.Comma) else None
    fixed = []
    for index, alias in enumerate(aliases):
        comma = alias.comma
        own = comma.whitespace_after if isinstance(comma, cst.Comma) else None
        if index == len(aliases) - 1:
            if closing is None:
                comma = cst.MaybeSentinel.DEFAULT
            elif isinstance(own, cst.ParenthesizedWhitespace) and isinstance(
                closing, cst.ParenthesizedWhitespace
            ):
                comma = comma.with_changes(
                    whitespace_after=closing.with_changes(first_line=own.first_line)
                )
            else:
                comma = cst.Comma(whitespace_after=closing)
        elif comma is cst.MaybeSentinel.DEFAULT:
            comma = cst.Comma(whitespace_after=cst.SimpleWhitespace(" "))
        fixed.append(alias.with_changes(comma=comma))
    return fixed
