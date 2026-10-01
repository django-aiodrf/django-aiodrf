"""
Test sessions.

``tests``               every Django/DRF combination DRF itself supports
``tests_minimum``       the oldest versions ``pyproject.toml`` allows
``tests_freethreaded``  CPython 3.14 without the GIL (``python3.14t``)
``tests_postgres``      the whole suite on PostgreSQL (``tests/services/compose.yaml``)
``canary``              the suite against Django's and DRF's main branches
``example``             the combined bookshop project through its ASGI application
``examples``            every independent example, with explicit service opt-ins
``unit``                isolated tests selected for the dev branch
``coverage``            aggregate existing reports; no repeated test pass
``workflows``           pinned actionlint through Go
``drf_parity``          DRF's own test suite with aiodrf's view layer
``ecosystem``           third-party packages of the Django/DRF ecosystem
``ecosystem_tenants``   django-tenants on PostgreSQL
``ecosystem_prometheus``  django-prometheus, on Django 6.0 (it declares Django < 6.1)
``ecosystem_elasticsearch``  django-elasticsearch-dsl against a live Elasticsearch
``ecosystem_mongodb``   django-mongodb-backend on MongoDB, Django 5.2 and 6.1
``ecosystem_valkey``    isolated native async cache contracts against Valkey
``ecosystem_redis``     native redis.asyncio and django-redis storage compatibility
``ecosystem_opensearch`` document preparation and native OpenSearch transport
``integrations``        isolated telemetry, Celery/Redis and S3 contracts
``lint``                pre-commit repository checks, Ruff and codespell
``docs``                strict MkDocs build without deployment
``typecheck``           mypy over ``src/aiodrf`` and typed consumer fixtures
``consumer``            positive/negative consumers against the installed wheel
``distribution``        wheel built from sdist, core and each extra independently
``migration``           executable DRF/adrf before/after applications
``adrf_compat``         an adrf project on ``aiodrf.contrib.adrf_compat``, without adrf
``native_db``           ``aiodrf.contrib.async_backend`` and the django-async-backend gates
``live``, ``live_postgres``  real HTTP contracts and local measurement reports
``resources``           isolated-process stream load and SIGTERM cleanup
``asgi_servers``        optional Uvicorn/Granian process and lifecycle contracts

Warnings are errors. Known upstream warnings have narrow compatibility contracts;
see examples/ECOSYSTEM.md. No global deprecation suppression is used.
"""

import os
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path

import nox

nox.options.default_venv_backend = "uv"
nox.options.reuse_existing_virtualenvs = True
nox.options.sessions = ["tests", "lint", "typecheck"]

PYTHONS = ["3.12", "3.13", "3.14"]
MATRIX = [
    ("5.2", "3.16"),
    ("5.2", "3.17"),
    ("5.2", "3.18"),
    ("6.0", "3.17"),
    ("6.0", "3.18"),
    ("6.1", "3.18"),
]
TEST_DEPS = ["pytest", "pytest-django", "pytest-asyncio", "pytest-cov", "hypothesis"]
OPTIONAL_DEPS = [
    "django-tasks>=0.12,<0.13",
    "drf-spectacular",
    "django-filter",
    "msgspec",
    "pydantic",
    "libcst",
    "opentelemetry-api",
]
ECOSYSTEM_REQUIREMENTS = "requirements/ecosystem/requirements.txt"
ECOSYSTEM_SUPPORT_REQUIREMENTS = "requirements/support/requirements.txt"
PYTEST = ["pytest", "-W", "error"]


def install(session, *requirements):
    session.install(*requirements, *TEST_DEPS, *OPTIONAL_DEPS)
    session.install("-e", ".", "--no-deps")


@nox.session(python=PYTHONS)
@nox.parametrize("django,drf", MATRIX, ids=[f"django{d}-drf{r}" for d, r in MATRIX])
def tests(session, django, drf):
    install(session, f"django~={django}.0", f"djangorestframework~={drf}.0")
    session.run(*PYTEST, *session.posargs)
    if not session.posargs:
        # tests/deployment is manual (it needs Uvicorn, Nginx, PostgreSQL),
        # except the harness's own tests, which need no service but import
        # its clients.
        session.install("httpx>=0.27", "psycopg[binary]>=3.2")
        session.run(*PYTEST, "tests/deployment/test_harness.py")


@nox.session(python="3.14")
@nox.parametrize(("django", "drf"), [("5.2", "3.16"), ("6.1", "3.18")])
def differential(session, django, drf):
    """The same requests to DRF's viewsets and to aiodrf's, compared."""
    install(session, f"django~={django}.0", f"djangorestframework~={drf}.0")
    session.run(*PYTEST, "tests/differential", *session.posargs)


@nox.session(python=PYTHONS)
@nox.parametrize("django,drf", MATRIX, ids=[f"django{d}-drf{r}" for d, r in MATRIX])
def unit(session, django, drf):
    """Isolated tests for dev branches, without database/service integration."""
    install(session, f"django~={django}.0", f"djangorestframework~={drf}.0")
    session.run(*PYTEST, "-m", "unit", *session.posargs)


@nox.session(python="3.14")
def examples(session):
    """Check each independent example with an isolated uv environment."""
    session.run("python", "examples/check.py", *session.posargs, external=True)


@nox.session(python="3.14")
def coverage(session):
    """Combine existing test-job data without executing the tests again."""
    session.install("coverage[toml]")
    session.run("coverage", "combine", "--keep", *session.posargs)
    session.run("coverage", "report", "--fail-under=85")
    session.run("coverage", "xml")
    session.run("coverage", "html")


@nox.session(python=False)
def workflows(session):
    """Validate GitHub workflow expressions, job graphs and shell commands."""
    session.run(
        "go", "run", "github.com/rhysd/actionlint/cmd/actionlint@v1.7.12", external=True
    )


@nox.session(python="3.12")
def tests_minimum(session):
    # The floors of ``[project] dependencies`` and of the extras.
    session.install(
        "django==5.2",
        "djangorestframework==3.16.0",
        "asgiref==3.8.1",
        "drf-spectacular==0.28.0",
        "django-filter==25.1",
        "msgspec==0.19.0",
        "pydantic==2.9.0",
        "libcst==1.4.0",
        "opentelemetry-api==1.27.0",
        # Not a floor: lets the cache codec tests run against the floors above.
        "redis",
        *TEST_DEPS,
    )
    session.install("-e", ".", "--no-deps")
    session.run(*PYTEST, *session.posargs)


@nox.session(python="3.14")
def canary(session):
    # The next releases, before they are released: incompatibilities and new
    # deprecations show here first. Not in the default sessions; a failure is
    # a finding to triage, not necessarily a defect of aiodrf.
    install(
        session,
        "django @ https://github.com/django/django/archive/refs/heads/main.tar.gz",
        "djangorestframework @ https://github.com/encode/django-rest-framework/archive/refs/heads/main.tar.gz",
    )
    session.run(
        "python",
        "-c",
        "import django, rest_framework; print(django.__version__, rest_framework.VERSION)",
    )
    session.run(*PYTEST, *session.posargs)


@nox.session(python="3.14")
@nox.parametrize(("django", "drf"), [("5.2", "3.16"), ("5.2", "3.17"), ("6.1", "3.18")])
def drf_parity(session, django, drf):
    # DRF's tests from the source release of the installed DRF, with aiodrf's
    # views swapped in (``tests/drf_parity/aiodrf_drf_parity.py``).
    session.install(
        f"django~={django}.0",
        f"djangorestframework~={drf}.0",
        "pytest",
        "pytest-django",
        # What DRF's own test requirements add; pytz for DRF 3.16 and 3.17.
        "dj-database-url",
        "importlib-metadata",
        "pytz",
    )
    session.install("-e", ".", "--no-deps")
    version = session.run(
        "python",
        "-c",
        "import rest_framework; print(rest_framework.VERSION)",
        silent=True,
    ).strip()
    source = Path(session.create_tmp()) / f"django-rest-framework-{version}"
    if not source.exists():
        url = f"https://github.com/encode/django-rest-framework/archive/refs/tags/{version}.tar.gz"
        with (
            urllib.request.urlopen(url) as response,
            tarfile.open(fileobj=response, mode="r|gz") as archive,
        ):
            archive.extractall(source.parent, filter="data")
        # The installed rest_framework is the one under test, not the checkout's.
        shutil.rmtree(source / "rest_framework")
    session.chdir(source)
    session.run(
        "python",
        "-m",
        "pytest",
        "-q",
        "-p",
        "no:cacheprovider",
        "-p",
        "aiodrf_drf_parity",
        *session.posargs,
        env={"PYTHONPATH": str(Path(__file__).parent / "tests" / "drf_parity")},
    )


@nox.session(python="3.14")
def example(session):
    # ``examples/bookshop``: the recommended setup, tested through the real
    # ASGI application with its lifespan, as a server runs it.
    # Schemathesis generates requests from the schema (tests/test_schema_fuzz.py).
    install(
        session,
        "django~=6.1.0",
        "djangorestframework~=3.18.0",
        "httpx",
        "asgi-lifespan",
        "schemathesis",
    )
    session.chdir("examples/bookshop")
    session.run("python", "manage.py", "check", "--fail-level", "WARNING")
    session.run("python", "manage.py", "makemigrations", "--check", "--dry-run")
    session.run(
        "python",
        "manage.py",
        "spectacular",
        "--validate",
        "--fail-on-warn",
        "--file",
        os.devnull,
    )
    session.run(*PYTEST, *session.posargs)


@nox.session(python="3.14t")
def tests_freethreaded(session):
    install(session, "django~=6.1.0", "djangorestframework~=3.18.0")
    # An extension module that does not declare free-threading support turns
    # the GIL back on with a RuntimeWarning, which ``-W error`` makes an
    # error. (``PYTHON_GIL=0`` would keep the GIL off and hide it.)
    session.run(*PYTEST, *session.posargs)


@nox.session(python="3.14")
def tests_postgres(session):
    # ``pool``: tests/postgres/test_pool_saturation.py uses Django's pool option.
    install(
        session, "django~=6.1.0", "djangorestframework~=3.18.0", "psycopg[binary,pool]"
    )
    # ``--no-migrations``: see tests/settings_postgres.py. Two runs, because
    # ``norecursedirs`` hides tests/postgres from a run that starts at tests/.
    for target in ("tests", "tests/postgres"):
        session.run(
            *PYTEST,
            "--no-migrations",
            target,
            *session.posargs,
            env={"DJANGO_SETTINGS_MODULE": "tests.settings_postgres"},
        )


@nox.session(python="3.14")
def native_db(session):
    # aiodrf.contrib.async_backend and the django-async-backend gate tests, on
    # PostgreSQL from tests/services/compose.yaml. ``--no-migrations``: see
    # tests/settings_postgres.py.
    install(
        session,
        "django~=6.1.0",
        "djangorestframework~=3.18.0",
        "django-async-backend[pool]~=6.1.5",
        "psycopg[binary,pool]",
        # Receivers that defer work with ``on_commit``, on native writes.
        "django-cleanup",
        "django-cacheops",
    )
    for target, settings in (
        ("tests/async_backend", "tests.async_backend.settings"),
        ("tests/native_db_gates", "tests.native_db_gates.settings"),
    ):
        session.run(
            *PYTEST,
            "--no-migrations",
            target,
            *session.posargs,
            env={"DJANGO_SETTINGS_MODULE": settings},
        )


@nox.session(python="3.14")
@nox.parametrize(("django", "drf"), [("5.2", "3.16"), ("6.1", "3.18")])
def adrf_compat(session, django, drf):
    # An adrf project on aiodrf.contrib.adrf_compat; adrf must not be installed.
    install(session, f"django~={django}.0", f"djangorestframework~={drf}.0")
    session.run(
        *PYTEST,
        # The project imports adrf's modules on purpose; test_compat.py tests
        # the warnings.
        "-W",
        "ignore::aiodrf.contrib.adrf_compat.AdrfCompatWarning",
        "tests/adrf_compat",
        *session.posargs,
        env={"DJANGO_SETTINGS_MODULE": "tests.adrf_compat.settings"},
    )


@nox.session(python="3.14")
def ecosystem(session):
    install(
        session,
        "django~=6.1.0",
        "djangorestframework~=3.18.0",
        "-r",
        ECOSYSTEM_REQUIREMENTS,
        "-r",
        ECOSYSTEM_SUPPORT_REQUIREMENTS,
    )
    # The cache tests again: here django-redis and redis-py are installed.
    session.run(
        *PYTEST,
        "tests/ecosystem",
        "tests/test_caches.py",
        *session.posargs,
        env={"DJANGO_SETTINGS_MODULE": "tests.ecosystem.settings"},
    )


@nox.session(python="3.14")
def ecosystem_redis(session):
    install(
        session,
        "django~=6.1.0",
        "djangorestframework~=3.18.0",
        "redis>=5.0.1",
        "django-redis>=7",
        "httpx",
        "asgi-lifespan",
    )
    session.run(
        *PYTEST,
        "tests/ecosystem/redis",
        "tests/test_native_cache.py",
        *session.posargs,
        env={
            "DJANGO_SETTINGS_MODULE": "tests.settings",
            "AIODRF_TEST_REDIS_URL": os.environ.get(
                "AIODRF_TEST_REDIS_URL", "redis://127.0.0.1:6380/14"
            ),
        },
    )


@nox.session(python="3.14")
def ecosystem_opensearch(session):
    """Optional server tests use AIODRF_TEST_OPENSEARCH_URL and unique indices."""
    install(
        session,
        "django~=6.1.0",
        "djangorestframework~=3.18.0",
        "django-opensearch-dsl>=0.8",
        "opensearch-py[async]>=3.2",
    )
    session.run(
        *PYTEST,
        "tests/ecosystem/opensearch",
        *session.posargs,
        env={"DJANGO_SETTINGS_MODULE": "tests.ecosystem.opensearch.settings"},
    )


@nox.session(python="3.14")
def ecosystem_valkey(session):
    """Isolate the vendor's request-finished hook from other cache suites."""
    install(
        session,
        "django~=6.1.0",
        "djangorestframework~=3.18.0",
        "django-valkey>=0.4.1",
        "httpx",
        "asgi-lifespan",
    )
    session.run(
        *PYTEST,
        "tests/ecosystem/valkey",
        *session.posargs,
        env={
            "DJANGO_SETTINGS_MODULE": "tests.settings",
            "AIODRF_TEST_VALKEY_URL": os.environ.get(
                "AIODRF_TEST_VALKEY_URL", "valkey://127.0.0.1:6381/14"
            ),
        },
    )


@nox.session(python="3.14")
def ecosystem_tenants(session):
    # django-tenants needs PostgreSQL (``tests/services/compose.yaml``) and a
    # project of its own: its database engine, router and middleware.
    install(
        session,
        "django~=6.1.0",
        "djangorestframework~=3.18.0",
        "django-tenants",
        "psycopg[binary]",
    )
    session.run(
        *PYTEST,
        "tests/ecosystem/tenants",
        *session.posargs,
        env={"DJANGO_SETTINGS_MODULE": "tests.ecosystem.tenants.settings"},
    )


@nox.session(python="3.14")
@nox.parametrize(("django", "backend"), [("5.2", "5.2.4"), ("6.1", "6.1.0")])
def ecosystem_mongodb(session, django, backend):
    # django-mongodb-backend is released per Django version. MongoDB from
    # ``tests/services/compose.yaml``: a replica set and a standalone server.
    install(
        session,
        f"django~={django}.0",
        "djangorestframework~=3.18.0",
        f"django-mongodb-backend~={backend}",
        "django-mongodb-extensions[rest-framework]~=0.3.0",
    )
    session.run(
        *PYTEST,
        "tests/ecosystem/mongodb",
        *session.posargs,
        env={"DJANGO_SETTINGS_MODULE": "tests.ecosystem.mongodb.settings"},
    )


@nox.session(python="3.14")
def ecosystem_prometheus(session):
    # django-prometheus 2.5.0 declares Django < 6.1; its middleware wraps the
    # project's, in a project of its own.
    install(
        session, "django~=6.0.0", "djangorestframework~=3.18.0", "django-prometheus"
    )
    session.run(
        *PYTEST,
        "tests/ecosystem/prometheus",
        *session.posargs,
        env={"DJANGO_SETTINGS_MODULE": "tests.ecosystem.prometheus.settings"},
    )


@nox.session(python="3.14")
def ecosystem_elasticsearch(session):
    # django-elasticsearch-dsl against a real server, run locally:
    # ``docker run -p 127.0.0.1:9200:9200 -e discovery.type=single-node
    # -e xpack.security.enabled=false elasticsearch:9.x``.
    # The settings of ``nox -s ecosystem`` install every package of it.
    install(
        session,
        "django~=6.1.0",
        "djangorestframework~=3.18.0",
        "-r",
        ECOSYSTEM_REQUIREMENTS,
        "-r",
        ECOSYSTEM_SUPPORT_REQUIREMENTS,
    )
    url = os.environ.get("AIODRF_TEST_ELASTICSEARCH_URL", "http://127.0.0.1:9200")
    session.run(
        *PYTEST,
        "tests/ecosystem/test_elasticsearch.py",
        "tests/ecosystem/test_elasticsearch_live.py",
        *session.posargs,
        env={
            "DJANGO_SETTINGS_MODULE": "tests.ecosystem.settings",
            "AIODRF_TEST_ELASTICSEARCH_URL": url,
        },
    )


@nox.session(python="3.14")
def integrations(session):
    install(
        session,
        "django~=6.1.0",
        "djangorestframework~=3.18.0",
        "httpx",
        "sentry-sdk",
        "opentelemetry-sdk",
        "opentelemetry-instrumentation-django",
        "opentelemetry-instrumentation-asgi",
        "celery[redis]",
        "django-storages[s3]",
    )
    session.run(*PYTEST, "tests/integrations", *session.posargs)


@nox.session(python="3.14")
def docs(session):
    """Build the public site without deploying it or loading runtime extras."""
    session.install("--group", "docs")
    session.run("mkdocs", "build", "--strict", *session.posargs)
    # A build can succeed without the theme's CSS and JavaScript.
    session.run("python", "-m", "tools.docs_site", "site")


@nox.session(python="3.14")
def lint(session):
    session.install("ruff", "codespell", "pre-commit")
    session.run("pre-commit", "run", "--all-files")
    session.run(
        "codespell",
        "src",
        "tests",
        "benchmarks",
        "examples",
        "docs",
        "tools",
        "README.md",
        "CONTRIBUTING.md",
        "CHANGELOG.md",
        "SECURITY.md",
        "CODE_OF_CONDUCT.md",
        "AI_POLICY.md",
        "RELEASE.md",
        "MAINTAINING.md",
    )


@nox.session(python="3.14")
def typecheck(session):
    # ``src/aiodrf``, unannotated bodies included, and typed consumers
    # (``[tool.mypy]``).
    install(
        session,
        "django~=6.1.0",
        "djangorestframework~=3.18.0",
        "mypy",
        "django-stubs",
        "djangorestframework-stubs",
        # Typed drivers of the native cache contribs.
        "redis",
        "valkey",
    )
    session.run("mypy", "src/aiodrf", "tests/typing/valid.py", env={"PYTHONPATH": "."})
    # A consumer under ``--strict``: no untyped aiodrf call, generic views.
    session.run(
        "mypy",
        "--config-file",
        "tests/typing/mypy-strict.ini",
        "tests/typing/strict.py",
        env={"PYTHONPATH": "."},
    )
    output = session.run(
        "mypy",
        "tests/typing/invalid.py",
        env={"PYTHONPATH": "."},
        success_codes=[1],
        silent=True,
    )
    if (
        not output
        or output.count(": error:") != 5
        or output.count("[arg-type]") != 5
        or 'Argument "raise_exception"' not in output
        or 'Argument "chunk_size"' not in output
    ):
        session.error(
            "The consumer fixture must report exactly its five invalid argument types."
        )


def build_wheel(session):
    project = Path(__file__).parent.resolve()
    # Reused nox environments may contain distributions of earlier versions.
    # Keep each build separate without deleting artifacts from previous runs.
    artifacts = Path(
        tempfile.mkdtemp(prefix="artifacts-", dir=session.create_tmp())
    ).resolve()
    session.install("build[uv]")
    session.run(
        "python",
        "-m",
        "build",
        str(project),
        "--installer",
        "uv",
        "--outdir",
        str(artifacts),
    )
    wheels = list(artifacts.glob("*.whl"))
    if len(wheels) != 1 or len(list(artifacts.glob("*.tar.gz"))) != 1:
        session.error("Expected exactly one wheel and one sdist")
    return project, wheels[0]


@nox.session(python="3.14")
@nox.parametrize(
    "extra",
    [
        "core",
        "msgspec",
        "pydantic",
        "spectacular",
        "filter",
        "codemod",
        "opentelemetry",
        "tasks",
        "whitenoise",
        "granian",
        "valkey",
        "redis",
        "opensearch",
        "async-backend",
    ],
)
def distribution(session, extra):
    project, wheel = build_wheel(session)
    session.install(str(wheel) + (f"[{extra}]" if extra != "core" else ""))
    session.chdir(session.create_tmp())
    session.run("python", str(project / "tests/distribution/smoke.py"), extra)


@nox.session(python="3.14")
def consumer(session):
    project, wheel = build_wheel(session)
    session.install(str(wheel), "mypy", "django-stubs", "djangorestframework-stubs")
    session.chdir(session.create_tmp())
    command = [
        "mypy",
        "--config-file",
        str(project / "tests/typing/mypy.ini"),
        "--no-incremental",
    ]
    environment = {"PYTHONPATH": str(project)}  # Test app only; deliberately no src/.
    session.run(*command, str(project / "tests/typing/valid.py"), env=environment)
    output = session.run(
        *command,
        str(project / "tests/typing/invalid.py"),
        env=environment,
        silent=True,
        success_codes=[1],
    )
    if not output or output.count("[arg-type]") != 5 or output.count(": error:") != 5:
        session.error(
            "Installed consumer fixture did not reject exactly its five invalid arguments"
        )
    session.install("msgspec", "pydantic")
    session.run(*command, str(project / "tests/typing/optional.py"), env=environment)


@nox.session(python="3.14")
def migration(session):
    install(session, "django~=6.1.0", "djangorestframework~=3.18.0", "adrf>=0.1.14")
    session.run(
        *PYTEST,
        "tests/test_migration_contract.py",
        "tests/test_codemod.py",
        *session.posargs,
    )


@nox.session(python=["3.14", "3.14t"])
def live(session):
    install(
        session,
        "django~=6.1.0",
        "djangorestframework~=3.18.0",
        "uvicorn>=0.30",
        "httpx>=0.27",
    )
    environment = {
        "DJANGO_SETTINGS_MODULE": "tests.settings_live",
        "AIODRF_LIVE_SQLITE": str(
            Path(session.create_tmp()).resolve() / "live.sqlite3"
        ),
    }
    session.run(
        *PYTEST, "tests/live/test_contracts.py", *session.posargs, env=environment
    )


@nox.session(python="3.14")
def asgi_servers(session):
    """Check optional server processes, request bodies, lifespan and disconnects."""
    install(
        session,
        "django~=6.1.0",
        "djangorestframework~=3.18.0",
        "httpx>=0.27",
        "-r",
        "requirements/servers/requirements.txt",
    )
    session.run(*PYTEST, "tests/live/test_servers.py", *session.posargs)


@nox.session(python=["3.14", "3.14t"])
def live_postgres(session):
    # psycopg-binary has no cp314t wheel. The Python implementation uses
    # system libpq and keeps the GIL off; the benchmark report records it.
    driver = "psycopg" if session.python == "3.14t" else "psycopg[binary]"
    install(
        session,
        "django~=6.1.0",
        "djangorestframework~=3.18.0",
        "uvicorn>=0.30",
        "httpx>=0.27",
        driver,
    )
    environment = {"DJANGO_SETTINGS_MODULE": "tests.settings_postgres"}
    if session.python == "3.14t":
        environment["PSYCOPG_IMPL"] = "python"
    session.run(
        *PYTEST,
        "--no-migrations",
        "tests/live/test_contracts.py",
        *session.posargs,
        env=environment,
    )


@nox.session(python=["3.14", "3.14t"])
def resources(session):
    install(
        session,
        "django~=6.1.0",
        "djangorestframework~=3.18.0",
        "uvicorn>=0.30",
        "httpx>=0.27",
    )
    session.run(*PYTEST, "tests/live/test_resource_load.py", *session.posargs)
