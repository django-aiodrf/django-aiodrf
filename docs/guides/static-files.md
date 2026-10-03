# Static files under ASGI

For Uvicorn behind Nginx, serving collected static assets from Nginx or a CDN
removes file delivery from application workers. Keep user-uploaded files in a
separate storage and authorization policy; neither static middleware is an
upload-security boundary.

## ServeStatic

ServeStatic is an optional deployment dependency, not an aiodrf middleware
replacement. The tested Django integration uses the vendor's ASGI middleware:

```python
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "servestatic.middleware.ServeStaticMiddleware",
    # Application middleware follows.
]
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "servestatic.storage.CompressedManifestStaticFilesStorage",
    },
}
```

Install `servestatic`, retain the application's actual default storage, and run
`manage.py collectstatic --noinput` during deployment. Add `servestatic` before
`django.contrib.staticfiles` in `INSTALLED_APPS` when using the vendor's runserver
override and configuration checks. Finder-based development profiles are not
production settings. See the [vendor configuration reference](https://archmonger.github.io/ServeStatic/latest/django/).

The current vendor implementation adapts disk operations through worker
threads. An async file iterator avoids Django's synchronous-iterator buffering
fallback; it does not make filesystem access a native asynchronous syscall.
Startup discovery and deployment-time compression still have CPU and I/O costs.

The [platform example](../../examples/ecosystem-platform/README.md) uses
ServeStatic by default. Through Django's ASGI handler, ServeStatic serves `GET`
and `HEAD` requests with ETags and `304` responses, ranges and gzip, answers
missing files, rejects parent paths and passes API requests on to Django.

## WhiteNoise compatibility

The aiodrf adapter remains in this package and is planned for future deprecation.
Use ServeStatic for new application-served static-file deployments.

[WhiteNoise issue 251](https://github.com/evansd/whitenoise/issues/251) discusses
the WSGI/ASGI mismatch. The version in the compatibility environment returns a
synchronous file iterator. Django's ASGI handler must consume that iterator
before sending it; the repository explicitly tests the resulting warning.

Install `django-aiodrf[whitenoise]` to use the optional dual-mode adapter:

```python
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "aiodrf.contrib.whitenoise.whitenoise_middleware",
    # Application middleware follows.
]
```

Replace, rather than duplicate, `whitenoise.middleware.WhiteNoiseMiddleware`.
All `WHITENOISE_*`, storage and collectstatic configuration stays with WhiteNoise.
The adapter delegates file lookup and response construction to the vendor. Under
ASGI that operation runs in a non-thread-sensitive worker; a miss awaits the
downstream application on its own loop. Cancellation drains an in-flight lookup
and closes any resulting response. It cannot forcibly interrupt a filesystem
operation, so cleanup may wait for that operation to finish.

This is middleware-call compatibility, **not native ASGI file streaming**.
WhiteNoise's synchronous response iterator is unchanged. Django still warns and
buffers it under ASGI, including empty HEAD/304 file responses. Do not suppress
the warning or replace Django response iteration globally. Use Nginx/CDN or
ServeStatic when asynchronous file iteration is required.

The platform example includes `project.settings_whitenoise` for the original
WSGI middleware and `project.settings_whitenoise_async` for this adapter.
The adapter delegates to WhiteNoise for synchronous and asynchronous requests,
reads files in a worker thread, and supports ranges, gzip and conditional
requests. It rejects path traversal and handles repeated cancellation. It does
not patch WhiteNoise or Django.
