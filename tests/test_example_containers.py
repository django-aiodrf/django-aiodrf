"""Container recipes cover every example without changing its application API."""

import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = ROOT / "examples"
COMPOSE = yaml.safe_load((EXAMPLES / "compose.yaml").read_text())


def test_every_project_has_an_isolated_image_and_loopback_port():
    ports = set()
    for path in EXAMPLES.glob("*/pyproject.toml"):
        name = path.parent.name
        service = COMPOSE["services"][name]
        assert service["build"] == {
            "context": "..",
            "dockerfile": "examples/Dockerfile",
            "args": {"EXAMPLE": name},
        }
        assert service["image"] == f"aiodrf-example-{name}:local"
        port = service["ports"][0]
        assert port.startswith("127.0.0.1:${EXAMPLE_PORT:-")
        assert port not in ports
        ports.add(port)
        assert service["init"] is True
        assert service["cap_drop"] == ["ALL"]
        assert "up --build --wait " + name in (path.parent / "README.md").read_text()


def test_infrastructure_is_private_and_requires_explicit_selection():
    for name, service in COMPOSE["services"].items():
        assert service["profiles"] == ["examples"]
        if "build" not in service:
            assert "ports" not in service, name
    mongo = COMPOSE["services"]["mongodb"]
    assert mongo["depends_on"]["mongodb-init"] == {
        "condition": "service_completed_successfully"
    }
    worker = COMPOSE["services"]["celery-worker"]
    api = COMPOSE["services"]["ecosystem-services"]
    assert worker["volumes"] == api["volumes"]
    assert (
        worker["environment"]["EXAMPLE_DB_PATH"]
        == api["environment"]["EXAMPLE_DB_PATH"]
    )
    assert (
        worker["environment"]["EXAMPLE_CELERY_BROKER"]
        == api["environment"]["EXAMPLE_CELERY_BROKER"]
    )
    assert worker["depends_on"]["ecosystem-services"] == {
        "condition": "service_healthy"
    }


def test_container_hosts_include_the_websocket_test_origin():
    platform = COMPOSE["services"]["ecosystem-platform"]
    assert "testserver" in platform["environment"]["EXAMPLE_ALLOWED_HOSTS"]


def test_build_context_excludes_upload_data_without_excluding_its_project():
    patterns = (ROOT / ".dockerignore").read_text().splitlines()
    assert "examples/uploads/uploads" in patterns
    assert "**/uploads" not in patterns
    for pattern in ("**/.env", "**/.venv", "**/.cache", "**/.hypothesis"):
        assert pattern in patterns


@pytest.fixture
def entrypoint(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "example_container_entrypoint", EXAMPLES / "container_entrypoint.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.delenv("DJANGO_SETTINGS_MODULE", raising=False)
    monkeypatch.delenv("EXAMPLE_CACHE", raising=False)
    monkeypatch.setattr(module.sys, "argv", ["container_entrypoint.py"])
    return module


@pytest.mark.parametrize(
    ("name", "project", "migration"),
    [
        ("basics", "project", "migrate"),
        ("bookshop", "bookshop", "migrate"),
        ("tenancy", "project", "migrate_schemas"),
    ],
)
def test_server_startup_migrates_then_hands_off_process(
    entrypoint, monkeypatch, name, project, migration
):
    monkeypatch.chdir(EXAMPLES / name)
    calls = []
    monkeypatch.setattr(
        entrypoint.subprocess, "run", lambda *a, **k: calls.append((a, k))
    )
    monkeypatch.setattr(entrypoint.os, "execv", lambda *a: calls.append(a))
    entrypoint.main()
    assert entrypoint.os.environ["DJANGO_SETTINGS_MODULE"] == f"{project}.settings"
    assert calls[0] == (
        ([entrypoint.sys.executable, "manage.py", migration, "--noinput"],),
        {"check": True},
    )
    assert calls[1] == (
        entrypoint.sys.executable,
        [
            entrypoint.sys.executable,
            "-m",
            "uvicorn",
            f"{project}.asgi:application",
            "--host",
            "0.0.0.0",  # noqa: S104 -- matches the internal container listener.
            "--port",
            "8000",
            "--lifespan",
            "on",
        ],
    )


def test_explicit_command_does_not_run_migrations(entrypoint, monkeypatch):
    monkeypatch.setattr(entrypoint.sys, "argv", ["entrypoint.py", "pytest", "-q"])
    run = Mock()
    monkeypatch.setattr(entrypoint.subprocess, "run", run)
    execvp = Mock(side_effect=SystemExit)
    monkeypatch.setattr(entrypoint.os, "execvp", execvp)
    with pytest.raises(SystemExit):
        entrypoint.main()
    execvp.assert_called_once_with("pytest", ["pytest", "-q"])
    run.assert_not_called()


def test_migration_failure_prevents_server_start(entrypoint, monkeypatch):
    run = Mock(side_effect=entrypoint.subprocess.CalledProcessError(1, "migrate"))
    monkeypatch.setattr(entrypoint.subprocess, "run", run)
    execvp = Mock()
    monkeypatch.setattr(entrypoint.os, "execv", execvp)
    with pytest.raises(entrypoint.subprocess.CalledProcessError):
        entrypoint.main()
    execvp.assert_not_called()


def test_database_cache_and_explicit_settings_are_preserved(entrypoint, monkeypatch):
    monkeypatch.setenv("EXAMPLE_CACHE", "database")
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "project.settings_search")
    run = Mock()
    monkeypatch.setattr(entrypoint.subprocess, "run", run)
    monkeypatch.setattr(entrypoint.os, "execv", Mock())
    entrypoint.main()
    assert entrypoint.os.environ["DJANGO_SETTINGS_MODULE"] == "project.settings_search"
    assert run.call_args_list[1].args[0][-1] == "createcachetable"
