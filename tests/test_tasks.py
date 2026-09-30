"""
Django Tasks (6.0+) and its Django 5 backport from aiodrf views. The API is async
already (``aenqueue``, ``acall``); these tests show that its backends run a
task where a request's code may run: a synchronous task in the request's
thread, an ``async def`` task on the event loop.
"""

import threading

import django
import pytest
from django.db import transaction
from django.test import override_settings
from django.urls import path
from rest_framework.permissions import AllowAny

from aiodrf.response import Response
from aiodrf.views import APIView
from tests.base import both_transports
from tests.testapp.models import Author

tasks = pytest.importorskip(
    "django.tasks" if django.VERSION >= (6, 0) else "django_tasks",
    reason="Django 5 uses the optional django-tasks backport",
)


@tasks.task
def record_sync(name):
    author = Author.objects.create(name=name)
    return {"id": author.pk, "thread": threading.get_ident()}


@tasks.task
async def record_async(name):
    author = await Author.objects.acreate(name=name)
    return {"id": author.pk, "thread": threading.get_ident()}


@tasks.task(backend="queued")
def later(name):
    raise AssertionError("a queued task does not run in the request")


class Open(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]


class Enqueue(Open):
    async def post(self, request):
        task = record_async if request.data.get("async") else record_sync
        result = await task.aenqueue(request.data["name"])
        return Response({"status": result.status, "value": result.return_value})


class Queue(Open):
    async def post(self, request):
        result = await later.aenqueue(request.data["name"])
        return Response({"status": result.status, "id": result.id})


class EnqueueSynchronously(Open):
    """The sync API from async code: Django's guard says no, as everywhere."""

    async def post(self, request):
        result = record_sync.enqueue(request.data["name"])
        errors = [error.exception_class_path for error in result.errors]
        return Response({"status": result.status, "errors": errors})


urlpatterns = [
    path("enqueue/", Enqueue.as_view()),
    path("queue/", Queue.as_view()),
    path("sync/", EnqueueSynchronously.as_view()),
]


@both_transports
class _TaskTests:
    @override_settings(ROOT_URLCONF=__name__)
    async def test_an_immediate_task_runs_off_the_loop(self):
        response = await self.api("post", "/enqueue/", data={"name": "Ursula"})
        assert response.status_code == 200, response.data
        assert response.data["status"] == "SUCCESSFUL"
        assert await Author.objects.filter(pk=response.data["value"]["id"]).aexists()
        assert response.data["value"]["thread"] != threading.get_ident()

    @override_settings(ROOT_URLCONF=__name__)
    async def test_an_async_task_runs_on_the_loop(self):
        response = await self.api(
            "post", "/enqueue/", data={"name": "Octavia", "async": True}
        )
        assert response.status_code == 200, response.data
        assert response.data["status"] == "SUCCESSFUL"
        assert await Author.objects.filter(pk=response.data["value"]["id"]).aexists()
        if self.transport == "asgi":
            assert response.data["value"]["thread"] == threading.get_ident()

    @override_settings(ROOT_URLCONF=__name__)
    async def test_a_queued_backend_only_records_the_task(self):
        response = await self.api("post", "/queue/", data={"name": "Nnedi"})
        assert response.status_code == 200, response.data
        assert response.data["status"] == "READY"

    @override_settings(ROOT_URLCONF=__name__)
    async def test_the_synchronous_api_runs_the_task_where_it_is_called(self):
        # ``ImmediateBackend`` runs the task where ``enqueue()`` is called and
        # records what it raised: in a coroutine that is Django's guard,
        # under WSGI as well (Django runs the coroutine in an event loop).
        # Async code enqueues with ``aenqueue()``.
        response = await self.api("post", "/sync/", data={"name": "x"})
        assert response.status_code == 200, response.data
        assert response.data == {
            "status": "FAILED",
            "errors": ["django.core.exceptions.SynchronousOnlyOperation"],
        }

    def test_enqueue_on_commit_does_not_run_after_rollback(self):
        name = "rolled back task"
        with (
            self.captureOnCommitCallbacks(execute=True) as callbacks,
            transaction.atomic(),
        ):
            transaction.on_commit(lambda: record_sync.enqueue(name))
            transaction.set_rollback(True)
        assert callbacks == []
        assert not Author.objects.filter(name=name).exists()

    def test_enqueue_on_commit_runs_after_success(self):
        name = "committed task"
        with (
            self.captureOnCommitCallbacks(execute=True) as callbacks,
            transaction.atomic(),
        ):
            transaction.on_commit(lambda: record_sync.enqueue(name))
            assert not Author.objects.filter(name=name).exists()
        assert len(callbacks) == 1
        assert Author.objects.filter(name=name).exists()
