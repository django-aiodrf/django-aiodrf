# Contributing

Thank you for helping improve django-aiodrf. Please open an issue to discuss
substantial API or integration changes before you start. A useful proposal
describes the application requirement, the Django or DRF alternatives you
considered, the extension point involved, compatibility risks and how the
change will be tested. Report security vulnerabilities as described in
[SECURITY.md](SECURITY.md), not in public issues.

## Development environment

Install [uv](https://docs.astral.sh/uv/), clone the repository and run:

```console
uv venv --python 3.14
uv pip install -e . --group dev
uv run --no-sync nox --list
uv run --no-sync nox -s 'unit-3.12(django5.2-drf3.16)'
uv run --no-sync nox -s lint typecheck
```

nox creates an isolated environment for each supported Python, Django and DRF
combination. The library and its examples do not commit `uv.lock`: install with
`uv pip` and run commands with `uv run --no-sync`, so that the declared version
ranges are what gets tested.

| Tool | Purpose | Command |
| --- | --- | --- |
| uv | Environments and dependency installation | `uv pip install -e . --group dev` |
| nox | Version matrices and service-backed test sessions | `uv run --no-sync nox --list` |
| Ruff, codespell | Linting, formatting and spelling | `uv run --no-sync nox -s lint` |
| mypy | Type checking of the package and of typed consumers | `uv run --no-sync nox -s typecheck` |
| pytest, coverage.py | Tests and branch coverage | `uv run --no-sync pytest`; `uv run --no-sync nox -s coverage` |
| actionlint | Workflow validation (requires Go 1.24+) | `uv run --no-sync nox -s workflows` |
| pre-commit | Repository hygiene checks | `uv run --no-sync pre-commit run --all-files` |
| MkDocs | Documentation site build | `uv run --no-sync nox -s docs` |
| build, twine | Distribution build and metadata check | `uv run --no-sync python -m build --installer uv`; `uv run --no-sync twine check dist/*` |

To run the checks before every commit, install the hook with
`uv run --no-sync pre-commit install`. Pre-commit inspects files known to git;
run Ruff directly on new files that are not yet added.

## Code standards

Code follows PEP 8 and PEP 257, formatted by Ruff (Black-compatible, 88
columns). Follow Django's model and `Meta` ordering, request-first view
signatures and DRF's usual extension hooks. Docstrings explain behaviour that
is not obvious from the code and who owns a resource; they do not restate each
line.

```console
uv run --no-sync ruff check . --fix
uv run --no-sync ruff format .
uv run --no-sync nox -s lint typecheck
```

- Use public Django and DRF APIs. When a private upstream name is unavoidable,
  add it to [upstream internals](docs/reference/upstream-internals.md) with a
  test that detects its removal.
- Do not monkeypatch Django or DRF, and do not assume that unknown synchronous
  code is safe to run on the event loop. Any runtime replacement must be
  recorded in the [runtime adaptation inventory](docs/reference/runtime-adaptations.md).
- Do not keep request state or loop-bound resources in mutable globals.
- Annotate public functions. Where Django's or DRF's type stubs conflict with an
  async override, prefer a wider annotation to a change of runtime behaviour.

The conventions follow [Django's coding style](https://docs.djangoproject.com/en/dev/internals/contributing/writing-code/coding-style/)
and [DRF's contribution guidance](https://www.django-rest-framework.org/community/contributing/),
with Ruff in place of Black and isort.

## Tests

### Test-driven development

Features and bug fixes are developed test first:

1. Write a focused test and run it against the unchanged code. Confirm that it
   fails for the reason the change addresses; a missing dependency or an
   unavailable database is not a reproduction.
2. Make the smallest change that passes the test, then run the surrounding
   module and the affected integration tests.
3. Refactor with the tests passing, and run the relevant supported-version
   sessions.

Describe the test, the commands and the before and after results in the pull
request, and mention any service-backed checks you could not run.

Cover DRF's reference behaviour, synchronous and asynchronous callers and the
relevant inheritance chains. Include validation errors and authorization
failures, not only successful requests. Optional features need a test of the
default, disabled path. For concurrency bugs, assert on resources and cleanup;
timing thresholds are not reliable regression tests.

### Selecting tests

Tests marked `unit` need no database or external service; everything else is an
integration test.

```console
uv run --no-sync pytest -m unit
uv run --no-sync pytest -m integration
uv run --no-sync nox -s tests
uv run --no-sync nox -s ecosystem integrations
uv run --no-sync nox -s tests_postgres native_db
uv run --no-sync nox -s differential
uv run --no-sync nox -s examples
```

Several sessions need the local services defined in
`tests/services/compose.yaml`. Point them only at dedicated test services:
their fixtures migrate and clean the databases they use. The Redis and Valkey
Sentinel and Cluster tests use their own services; see
`tests/services/cache-topologies.md`.

The suite also runs with the benchmark's tuned settings
(`AIODRF_TEST_PROFILE=tuned`, `tuned-drf-fallback` or `tuned-python`, see
`tests/profiles.py`).
A test that verifies another configuration, such as thread-mode representation
or a serializer that cannot be compiled, declares the settings it depends on
with `@pytest.mark.aiodrf_settings(...)`, so that it keeps testing them under
any profile.

Warnings are errors. A test that needs an upstream deprecation warning must
assert it or filter exactly that warning for the affected package versions.

Coverage includes branches and every runtime module, with a minimum of 85 %.
A local report covers only the tests you ran:

```console
COVERAGE_FILE=.coverage.core uv run --no-sync pytest --cov=aiodrf --cov-report=term-missing
uv run --no-sync nox -s coverage
```

### Dependencies

Integration tests install two requirement files together:

- [Django and DRF integrations](requirements/ecosystem/requirements.txt), such
  as Channels and authentication packages.
- [Framework-neutral packages](requirements/support/requirements.txt), such as
  serializer engines, HTTP clients and instrumentation.

Both use minimum versions and do not pin transitive dependencies. Update a
dependency in its own change and run the affected integration session; a
successful installation alone does not show compatibility.

## Documentation and examples

Update the relevant guide, the [API reference](docs/api-reference.md), the
[settings reference](docs/reference/settings.md) and a runnable example with
every public change. A new setting documents its default, accepted values, when
it takes effect and how it fails. Describe defaults, opt-ins and limits in
concrete terms, and avoid promotional language and unmeasured performance
claims. Write for the people using the library: describe current behaviour, not
the history of its development.

Check the documentation with:

```console
uv run --no-sync python -m tools.llms
uv run --no-sync pytest -q tests/test_docs.py tests/test_documentation_tools.py tests/test_examples_catalogue.py
uv run --no-sync nox -s docs
```

The tests check local links, `python -m` targets, the API and settings
references and the example catalogue; `nox -s docs` builds the site with
`mkdocs build --strict`. `python -m tools.llms` regenerates `llms.txt`, an index
of the public documentation. `tools/docs_site.py` lists the files the site
publishes; add a source file to it only when a guide links to it.

The examples have [Docker recipes](examples/CONTAINERS.md). When you change an
example's dependencies, settings or entry point, build its image and run its
tests in that environment.

## Branches

Development happens on `dev`; open pull requests against it. `dev` runs the
quality checks and unit tests. `main` holds released and release-ready code
and changes only by a pull request from `dev`, which runs the whole test
matrix; releases are tagged on `main` ([RELEASE.md](RELEASE.md)).

## Pull requests

Every user-visible change updates [CHANGELOG.md](CHANGELOG.md) in the same pull
request. Pull requests are reviewed and must pass the automated checks before
they are merged. Documentation-only changes need the documentation checks above,
not an artificial failing runtime test.

AI-assisted contributions follow [AI_POLICY.md](AI_POLICY.md): you remain
responsible for the whole change and its verification.

Maintainers update aiodrf for new Django, DRF and asgiref releases as described
in [MAINTAINING.md](MAINTAINING.md), and publish releases as described in
[RELEASE.md](RELEASE.md).
