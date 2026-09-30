# Tasks on Django 5 and Django 6

Django 5.2 uses the optional `django-tasks` backport; Django 6 uses
`django.tasks`. Both already provide `aenqueue()`. No aiodrf runtime wrapper,
import alias or worker is needed. This integration consists of an optional
dependency, executable examples and the same request/transaction contract tests.

| Runtime | Application import | Immediate backend |
| --- | --- | --- |
| Django 5.2 | `from django_tasks import task` | `django_tasks.backends.immediate.ImmediateBackend` |
| Django 6+ | `from django.tasks import task` | `django.tasks.backends.immediate.ImmediateBackend` |

Install `django-aiodrf[tasks]` only for the backport. The tested backport line is
0.12.x; the extra deliberately has an upper bound because it is pre-1.0.
Its import currently calls `django_stubs_ext.monkeypatch()` for typing
compatibility. aiodrf does not add a patch or import the backport automatically.
The default installation does not acquire this dependency.

```python
from django_tasks import task  # Django 5; use django.tasks on Django 6.


@task
def summarize(article_id):
    # Application-owned synchronous work; the selected backend owns execution.
    return {"article_id": article_id}


# Inside an async view:
# result = await summarize.aenqueue(article.pk)
```

Set `TASKS` with the matching backend namespace. Do not configure
`django.tasks...` on Django 5 or silently switch namespaces with an import shim.
The [Django 5 example](../../examples/tasks-django5/README.md) and
[Django 6 example](../../examples/tasks-django6/README.md) are independent uv
projects. Each includes validated input, async/sync task functions, a dummy
queue and HTTP tests.

`ImmediateBackend` executes before the response and is not a background worker.
`DummyBackend` records tasks without executing them and is not durable. For
production select a maintained backend with the required worker, retries,
result retention, authorization and operational monitoring. Enqueueing is not
proof of eventual execution. These are the upstream
[Tasks API responsibilities](https://docs.djangoproject.com/en/6.0/topics/tasks/)
and the [backport's documented contract](https://github.com/RealOrangeOne/django-tasks).

When a task reads newly written rows, register synchronous `enqueue()` with
`transaction.on_commit()` inside the same synchronous save unit. Never hold
`transaction.atomic()` across arbitrary awaits. Rollback must discard the
callback; a queue failure after commit cannot undo the committed row. Use a
transactional outbox if atomic database/queue delivery is required.

Under both ASGI and WSGI, synchronous task work stays off the event loop, async
tasks are awaited, tasks sent to a queue are not run in the request, calling the
synchronous API from async code triggers Django's safety check, and callbacks
registered with `on_commit` enqueue only when the transaction commits. How
reliably tasks are delivered depends on the broker and worker you choose.
