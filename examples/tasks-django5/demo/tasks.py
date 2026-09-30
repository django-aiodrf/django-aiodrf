"""Task definitions using the tasks django5 queue API."""

from django_tasks import task


@task
def double(value):
    return value * 2


@task
async def async_double(value):
    return value * 2


@task(backend="queued")
def queued(value):
    raise AssertionError("DummyBackend must not execute this task")
