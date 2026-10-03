# Versioning and compatibility

This page explains which Django, DRF and Python versions aiodrf supports, how
versions are numbered, which parts of the package are covered by that promise
and how features are deprecated.

## Supported versions

aiodrf follows DRF's support policy. It supports:

- the Django versions supported by the DRF releases it supports;
- the Python versions supported by those Django versions, including the
  free-threaded build of CPython 3.14 where the dependencies provide wheels.

When Django or DRF ends support for a version, the next minor release of aiodrf
drops it and says so in the changelog.

| Django | DRF | Python |
| --- | --- | --- |
| 5.2 | 3.16, 3.17, 3.18 | 3.12, 3.13, 3.14 |
| 6.0 | 3.17, 3.18 | 3.12, 3.13, 3.14 |
| 6.1 | 3.18 | 3.12, 3.13, 3.14 |

Every combination in the table is tested, together with the oldest dependency
versions the package allows and CPython 3.14 without the GIL. The development
versions of Django and DRF are tested weekly to detect upcoming changes early;
they are not supported.

## Version numbers

aiodrf uses [Semantic Versioning](https://semver.org/) and stays on 0.x until
1.0:

- **Minor releases (0.x.0)** add features and remove APIs whose deprecation
  period has ended. Before 1.0, a minor release may also change behaviour in an
  incompatible way; the changelog lists every such change under "Changed" or
  "Removed" with the steps to take.
- **Patch releases (0.x.y)** contain bug fixes and documentation changes only.
- **1.0** will be released when the public API is stable. From then on,
  incompatible changes wait for a major release.

## Public API

The public API is what this documentation describes. Names starting with an
underscore are internal, and so are modules of `aiodrf.contrib` marked as
experimental, such as `ConcurrentListSerializer`.

### API inventory

| Tier | Scope | Compatibility policy |
| --- | --- | --- |
| Application API | The modules named after DRF's (`views`, `generics`, `mixins`, `viewsets`, `serializers`, `routers`, `decorators`, `permissions`, `authentication`, `throttling`, `pagination`, `request`, `response`, `test`), `aiodrf.aio`, `aiodrf.asgi`, `aiodrf.cache`, `aiodrf.management` (`AsyncCommand`, `acall_command`), the `AIODRF` settings, the documented classes of the contrib modules, `python -m aiodrf.codemod` and the management commands | Versioned as described above |
| Extension API | `aiodrf.utils`: `async_safe`, `register_pure`, `register_pure_method`, `run_sync`, `count_hops`; `aiodrf.authentication.register_credentials_check` | Versioned as described above; for code that tells aiodrf about a project's classes |
| Internal | Everything else: names not in a module's `__all__` (for example `resolve_pair`, `definer`, `class_cache` and `bridge_base` in `aiodrf.utils`), names starting with an underscore, the private modules of `aiodrf.aio` and of the modules django-fastdrf maintains (its compiler, input recognition and converter), which follow django-fastdrf's own policy | None: these may change in any release |

Each module's `__all__` lists its application or extension API.

## Deprecations

aiodrf deprecates features the way Django does: a deprecated feature keeps
working and emits a warning for two minor releases, and is removed in the
following one.

1. The release that deprecates a feature keeps it working, emits a warning that
   names the replacement, and lists it under "Deprecated" in the changelog.
2. The next minor release still includes it, with the warning.
3. The minor release after that removes it and lists it under "Removed".

Each planned removal has its own warning class, named after the release that
removes it, for example:

```python
class RemovedInAiodrf03Warning(DeprecationWarning):
    """Deprecated in 0.1, removed in 0.3."""
```

Run your tests with warnings turned into errors (`python -W error -m pytest`)
to find uses of deprecated APIs before they are removed.

aiodrf follows the deprecations Django and DRF announce. For example, it
supports DRF 3.18's `LIST_SERIALIZER_ERRORS_AS_DICT` setting and the
`RemovedInDRF320Warning` that comes with it.

## Changelog

The [changelog](../../CHANGELOG.md) lists every user-visible change of each
release under Added, Changed, Deprecated, Removed and Fixed.
