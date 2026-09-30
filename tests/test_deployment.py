from unittest.mock import patch

from django.core.checks import Tags, run_checks
from django.test import override_settings

from aiodrf.checks import check_asgi_deployment
from aiodrf.utils import count_hops, run_sync


class SyncOnlyMiddleware:
    sync_capable = True
    async_capable = False

    def __init__(self, get_response):
        raise AssertionError("A deployment check must not construct middleware")


@override_settings(ASGI_APPLICATION="project.asgi.application")
def test_deployment_checks_are_explicit_and_do_not_open_connections():
    with (
        override_settings(MIDDLEWARE=[f"{__name__}.SyncOnlyMiddleware"]),
        patch("aiodrf.checks.connections") as connections,
    ):
        connections.settings = {
            "default": {"CONN_MAX_AGE": 60},
            "other": {"CONN_MAX_AGE": 0},
        }
        assert {message.id for message in check_asgi_deployment(None)} == {
            "aiodrf.W004",
            "aiodrf.W005",
        }
        connections.__getitem__.assert_not_called()
        assert "aiodrf.W005" not in {
            message.id for message in run_checks(tags=[Tags.compatibility])
        }
        assert "aiodrf.W005" in {
            message.id
            for message in run_checks(
                tags=[Tags.compatibility], include_deployment_checks=True
            )
        }


@override_settings(ASGI_APPLICATION=None, MIDDLEWARE=[f"{__name__}.SyncOnlyMiddleware"])
def test_no_topology_is_inferred_without_an_asgi_setting():
    assert check_asgi_deployment(None) == []


async def test_hop_diagnostics_never_use_a_callables_repr():
    class Secret:
        def __repr__(self):
            raise AssertionError("Do not inspect application data")

        def __call__(self):
            return 42

    with count_hops() as hops:
        assert await run_sync(Secret())() == 42
    assert hops.calls == [Secret.__qualname__]


def test_atomic_save_on_mongodb_needs_the_contrib():
    # django-mongodb-backend's ``transaction.atomic`` does nothing: without
    # ``aiodrf.contrib.mongodb`` a failed save is not rolled back.
    from aiodrf.checks import check_mongodb_transactions

    with patch("aiodrf.checks.connections") as connections:
        connections.settings = {
            "default": {"ENGINE": "django.db.backends.sqlite3"},
            "documents": {"ENGINE": "django_mongodb_backend"},
        }
        warnings = check_mongodb_transactions(None)
        assert [(w.id, w.obj) for w in warnings] == [("aiodrf.W007", "documents")]
        connections.__getitem__.assert_not_called()
        with override_settings(AIODRF={"ATOMIC_SAVE": False}):
            assert check_mongodb_transactions(None) == []
        connections.settings = {"default": {"ENGINE": "django.db.backends.sqlite3"}}
        assert check_mongodb_transactions(None) == []
    assert "aiodrf.W007" not in {m.id for m in run_checks(tags=[Tags.compatibility])}
