"""
Opt-in patches of Django REST framework.

aiodrf patches nothing by default. Each patch here changes one member of a DRF
class for the whole process, so that every DRF serializer or response gains
what aiodrf's own classes do, and keeps DRF's public behavior; what it changes
is described below. They are applied at startup (``AppConfig.ready``) when
named in the setting::

    AIODRF = {"MONKEYPATCHES": ["weak_list_children", "release_drf_responses"]}

``apply()`` and ``revert()`` apply and undo them explicitly (tests).

``weak_list_children``
    ``ListSerializer.__init__`` binds the child to a weak proxy of the list
    (:mod:`aiodrf.contrib.list_serializers`), for DRF's, aiodrf's and
    third-party list serializers. The list and its instances are freed by
    reference counting instead of the cyclic garbage collector.
    ``child.parent is serializer`` is False (the proxy); ``==`` holds.

``release_drf_responses``
    A closed DRF ``Response`` cuts the back-references among its request
    objects, as aiodrf's ``Response`` does for aiodrf's views: the response in
    its ``renderer_context``, the view's ``response`` and ``head`` alias, the
    view and request in ``parser_context``, and the cycles of the serializer
    whose data it returned. ``data`` and the context's view and request stay
    readable after ``close()``.

``cache_model_field_info``
    ``rest_framework.utils.model_meta.get_field_info(model)``, which DRF's
    ``ModelSerializer`` calls to build its fields (for every instance, and in
    ``create``/``update``), is computed once per model. The answer is kept
    with the lists Django's ``Options`` caches (``fields``, ``many_to_many``,
    ``related_objects``) and the primary key; when Django recomputes them
    (``_expire_cache``, ``apps.clear_cache``), so does this. The same
    ``FieldInfo`` is returned to every caller: DRF only reads it.

``keep_json_encoders``
    ``JSONRenderer.render`` (DRF's and its subclasses') keeps the encoder its
    ``json.dumps`` builds for every call, one per configuration
    (``encoder_class``, ``ensure_ascii``, ``compact``, ``strict``): the same
    bytes. With an indentation (``Accept`` parameters, ``indent`` in the
    context), a subclass's own ``get_indent`` or attributes set on the
    renderer instance, DRF's ``render`` runs unchanged. JSON encoders keep no
    state between ``encode()`` calls; the standard library shares its own.

A patch making every field's ``parent`` weak was measured and left out: DRF
reads ``parent`` for each field of each object (``root``, ``context``), and a
Python descriptor there cost more than the collector's work it saved.
"""

import functools
import json
import operator
import threading
from collections.abc import Callable, Mapping
from typing import Any

from django.core.exceptions import ImproperlyConfigured
from fastdrf.renderers import _dumps_encoder
from rest_framework import renderers, response, serializers
from rest_framework.utils import model_meta

__all__ = ["PATCHES", "TARGETS", "applied", "apply", "revert"]


def _weak_list_children(original: Callable[..., Any]) -> Callable[..., Any]:
    from fastdrf.list_serializers import bind_child_weakly

    @functools.wraps(original)
    def __init__(self: serializers.ListSerializer, *args: Any, **kwargs: Any) -> None:
        original(self, *args, **kwargs)
        bind_child_weakly(self)

    return __init__


def _release_drf_responses(original: Callable[..., Any]) -> Callable[..., Any]:
    from fastdrf.response import _release

    @functools.wraps(original)
    def close(self: response.Response) -> None:
        original(self)
        _release(self)

    return close


def _cache_model_field_info(original: Callable[..., Any]) -> Callable[..., Any]:
    @functools.wraps(original)
    def get_field_info(model: Any) -> Any:
        # DRF reads the concrete model's options; DRF's ``update()`` passes an
        # instance. The answer lives on those options, as long as the model.
        opts = model._meta.concrete_model._meta
        state = (opts.fields, opts.many_to_many, opts.related_objects, opts.pk)
        kept = vars(opts).get(_FIELD_INFO)
        if kept is not None and all(map(operator.is_, kept[0], state)):
            return kept[1]
        info = original(model)
        vars(opts)[_FIELD_INFO] = (state, info)
        return info

    return get_field_info


_FIELD_INFO = "_aiodrf_drf_field_info"


def _keep_json_encoders(original: Callable[..., Any]) -> Callable[..., Any]:
    # (encoder_class, ensure_ascii, compact, strict) -> the encoder DRF builds
    encoders: dict[tuple, json.JSONEncoder] = {}
    drf_get_indent = renderers.JSONRenderer.get_indent

    @functools.wraps(original)
    def render(
        self: renderers.JSONRenderer,
        data: Any,
        accepted_media_type: str | None = None,
        renderer_context: Mapping[str, Any] | None = None,
    ) -> bytes:
        if (
            data is None
            or vars(self)
            or type(self).get_indent is not drf_get_indent
            # Media-type parameters or the context may ask for an indentation.
            or (accepted_media_type and ";" in accepted_media_type)
            or (renderer_context and renderer_context.get("indent") is not None)
        ):
            return original(self, data, accepted_media_type, renderer_context)
        key = (self.encoder_class, self.ensure_ascii, self.compact, self.strict)
        encoder = encoders.get(key)
        if encoder is None:
            # ``json.dumps``'s arguments in DRF's ``render``, the rest default.
            encoder = encoders.setdefault(
                key,
                _dumps_encoder(self),
            )
        ret = encoder.encode(data)
        return ret.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029").encode()

    return render


#: Patch name -> a function of the original member returning its replacement.
PATCHES = {
    "weak_list_children": _weak_list_children,
    "release_drf_responses": _release_drf_responses,
    "cache_model_field_info": _cache_model_field_info,
    "keep_json_encoders": _keep_json_encoders,
}
#: Patch name -> (class, member) it replaces.
TARGETS = {
    "weak_list_children": (serializers.ListSerializer, "__init__"),
    "release_drf_responses": (response.Response, "close"),
    "cache_model_field_info": (model_meta, "get_field_info"),
    "keep_json_encoders": (renderers.JSONRenderer, "render"),
}

# The class's own member before the patch, or none (DRF's
# ``Response`` inherits ``close`` from Django's ``HttpResponseBase``).
_MISSING = object()
_originals: dict[str, object] = {}
_lock = threading.Lock()


def _known(names: tuple[str, ...]) -> tuple[str, ...]:
    unknown = [name for name in names if name not in PATCHES]
    if unknown:
        raise ImproperlyConfigured(
            f"Unknown aiodrf monkeypatch {', '.join(map(repr, unknown))}; "
            f"expected {', '.join(map(repr, PATCHES))}."
        )
    return names


def apply(*names: str) -> None:
    """Apply the patches ``names``; one already applied is left as it is."""
    with _lock:
        for name in _known(names):
            if name in _originals:
                continue
            owner, member = TARGETS[name]
            _originals[name] = owner.__dict__.get(member, _MISSING)
            setattr(owner, member, PATCHES[name](getattr(owner, member)))


def revert(*names: str) -> None:
    """Restore DRF's own members of the patches ``names`` that are applied."""
    with _lock:
        for name in _known(names):
            if name not in _originals:
                continue
            original = _originals.pop(name)
            owner, member = TARGETS[name]
            if original is _MISSING:
                delattr(owner, member)
            else:
                setattr(owner, member, original)


def applied() -> list[str]:
    """The names of the applied patches."""
    with _lock:
        return list(_originals)
