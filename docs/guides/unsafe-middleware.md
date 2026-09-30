# Optional synchronous middleware grouping

Default: off. This is a scheduling experiment, not a requirement for aiodrf.
Use stock Django middleware unless your own measurements justify opting in.

Enabling it requires asgiref 3.12.1 or newer. aiodrf's own dependency floor is
unchanged; older asgiref works with the option off. Opting in on an older
asgiref fails configuration validation instead of falling back.

The subclasses in `aiodrf.unsafe.middleware` inherit Django's hooks unchanged.
When enabled, they declare `async_capable=False`, and Django adapts contiguous
sync middleware as one chain instead of dispatching each synchronous
`MiddlewareMixin` hook separately. `process_view` and exception handling stay
Django's. There is no monkey patch, replacement ASGI handler, executor pool,
connection sharing or disabled async safety guard.

This uses Django's [documented middleware capability flags](https://docs.djangoproject.com/en/6.1/topics/http/middleware/#asynchronous-support).
It is under `unsafe` because changing scheduling is a deployment-level opt-in;
it does not make synchronous code safe on the event loop.

## Enable and roll back

Keep the middleware order and the project's and third-party middleware.
Replace only the stock classes to group:

```python
AIODRF = {
    "UNSAFE_SYNC_MIDDLEWARE": False,  # Change to True only after local validation.
}

MIDDLEWARE = [
    "aiodrf.unsafe.middleware.SecurityMiddleware",
    "aiodrf.unsafe.middleware.SessionMiddleware",
    "aiodrf.unsafe.middleware.CommonMiddleware",
    "aiodrf.unsafe.middleware.CsrfViewMiddleware",
    "aiodrf.unsafe.middleware.AuthenticationMiddleware",
    "aiodrf.unsafe.middleware.MessageMiddleware",
    "aiodrf.unsafe.middleware.XFrameOptionsMiddleware",
]
```

Add the option to the existing `AIODRF` mapping. Both the middleware paths and
`True` are required; the flag alone changes no stock Django class. With
`False`, these subclasses keep Django's sync/async capability.

The handler reads capabilities when it builds its stack, so restart workers
after changing the setting. Roll back by setting it to `False` and restarting,
or by restoring Django's paths. `check --deploy` still reports `aiodrf.W005`
for enabled sync-only middleware; do not silence the async deployment warnings
to hide it.

## Tradeoffs and compatibility

- A sync call stack occupies the request's worker while an async view awaits;
  concurrency still needs threads. Size worker processes, admission limits and
  database pools for the workload.
- Third-party async middleware between groups can add boundaries. Do not
  replace custom middleware by its similarly named stock counterpart, and do
  not change the order to improve a benchmark.
- WSGI, synchronous views, Django session storage, CSRF, message storage,
  security headers and redirects still use the original implementations.
  Database work remains thread-sensitive; `CONN_MAX_AGE=0` still applies to
  ASGI deployment.
- Custom synchronous hooks stay in a worker. A subclass with its own async
  `__call__` or capability contract needs its own review.
- The tests cover concurrent request ContextVars/thread affinity, async view
  cancellation and streaming disconnects. Blocking synchronous hooks cannot
  be forcibly canceled; a stuck third-party hook can still delay cleanup.
- Compatibility with APM agents, servers, other middleware and future Django
  versions is not tested. Verify instrumentation and long-lived streams in the
  deployment that enables it.

Compare stock and grouped stacks for both DRF and aiodrf. Grouping can speed
up both, so grouped aiodrf against stock DRF says nothing about the gap between
the two in equal configurations.

## Trying it

The [middleware example](../../examples/middleware-experiment/README.md) runs
the same application with Django's standard middleware and with the grouped
variant, as separate profiles. To compare their performance, keep CPU
affinity, dependencies and server configuration identical and follow the
[performance guide](performance.md).
