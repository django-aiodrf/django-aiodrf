"""
The private core names the contribs use, and why.

A contrib ships with the core and may use its private names, but each use ties
the contrib to how the core works today. Every such use is listed below with
its reason; a contrib that adds one without listing it fails this test. Prefer
the public or extension API; once a second contrib needs a registry the core
owns, the core provides a function to write to it.
"""

import ast
from pathlib import Path

SOURCE = Path(__file__).parent.parent / "src/aiodrf"

# (contrib, core module, name): reason.
ALLOWED = {
    ("adrf_compat", "views", "_constructs_inline"): (
        "builds legacy permissions where aiodrf's own check would"
    ),
    ("adrf_compat", "policies", "_throttle_wait"): (
        "asks a refusing throttle's wait() where aiodrf's own check would"
    ),
    ("async_backend", "generics", "_CHECK_OBJECT"): (
        "the sync/async pair names of the generic actions it reimplements"
    ),
    ("async_backend", "generics", "_QUERYSET"): (
        "the sync/async pair names of the generic actions it reimplements"
    ),
    ("async_backend", "mixins", "_PAGINATED_RESPONSE"): (
        "the sync/async pair names of the generic actions it reimplements"
    ),
    ("async_backend", "generics", "_FETCH_MODES"): (
        "applies FETCH_MODE to native querysets"
    ),
    ("compiler", "aio._classify", "is_static"): (
        "the rule for serializers whose fields are a function of their class, "
        "owned by the classification cache, so that both keep per-class answers "
        "for the same serializers"
    ),
    ("compiler", "aio._classify", "fields_from_class"): (
        "a generic view's validated serializer has its class's fields, so its "
        "class's encoder applies"
    ),
    ("compiler", "aio._classify", "has_async_representation"): (
        "a serializer with asynchronous representation is not compiled"
    ),
    ("inputs", "aio._classify", "is_static"): (
        "input recognizers are kept per class for the same serializers as the "
        "classification cache"
    ),
    ("compiler", "aio._classify", "_BUILTIN_FIELDS"): (
        "DRF's exact field classes, captured when classification loads; with "
        "utils.is_bridge_base it tells framework methods from overrides"
    ),
    ("builtin", "aio._classify", "has_async_representation"): (
        "whether concurrent or batch-enriched items need the async walk"
    ),
    ("builtin", "aio._represent", "_alist"): (
        "materializes batch-enrichment input off the loop"
    ),
    ("builtin", "aio._classify", "_FIELD_HOOKS"): (
        "reuses the core's static-field rule for template and query caches"
    ),
    ("builtin", "aio._classify", "_model_fields_call_code"): (
        "reuses the core's static-field rule for template and query caches"
    ),
    ("mongodb", "aio._save", "_ATOMIC_FACTORIES"): (
        "registers MongoDB's transaction for ATOMIC_SAVE, whose "
        "transaction.atomic() is a no-op on that backend"
    ),
    ("msgspec", "response", "_PAYLOAD_CHECKED_RENDERERS"): (
        "adds its renderer to those checked for lazy payloads"
    ),
    ("msgspec", "response", "_KEPT_ENCODER_RENDERERS"): (
        "its variant that skips get_indent when it can only answer None"
    ),
    ("msgspec", "response", "_without_indent"): (
        "its variant that skips get_indent when it can only answer None"
    ),
    ("monkeypatches", "response", "_release"): (
        "releases a closed DRF response's cycles as aiodrf's Response.close() does"
    ),
    ("monkeypatches", "response", "_dumps_encoder"): (
        "builds the encoder DRF's JSONRenderer.render builds"
    ),
    ("opentelemetry", "utils", "_transparent"): (
        "its phase wrappers are transparent to pair resolution"
    ),
}


def _private_imports():
    found = set()
    for path in (SOURCE / "contrib").rglob("*.py"):
        contrib = path.relative_to(SOURCE / "contrib").parts[0].removesuffix(".py")
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.ImportFrom) or not node.module:
                continue
            module = node.module
            if not module.startswith("aiodrf.") or module.startswith("aiodrf.contrib"):
                continue
            private_module = any(part.startswith("_") for part in module.split("."))
            for alias in node.names:
                if alias.name.startswith("_") or private_module:
                    found.add((contrib, module.removeprefix("aiodrf."), alias.name))
    return found


def _listed(contrib, module, name):
    return any(
        (contrib, listed_module, name) in ALLOWED
        for listed_module in (module, module.removeprefix("aio."), f"aio.{module}")
    )


def test_every_private_core_name_a_contrib_uses_is_listed():
    unlisted = [
        (contrib, module, name)
        for contrib, module, name in sorted(_private_imports())
        if not _listed(contrib, module, name)
    ]
    assert unlisted == []
