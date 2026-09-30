# Authentication, object permissions and audit records

This project combines django-filter, django-guardian, djangorestframework-guardian,
rules, django-simple-history, django-auditlog, django-oauth-toolkit, Djoser,
dj-rest-auth, django-allauth, django-cors-headers and drf-spectacular. Vendor
authentication and permission classes are used unchanged on aiodrf views.

## Run with Docker

From the repository root:

```console
docker compose -f examples/compose.yaml up --build --wait ecosystem-security
docker compose -f examples/compose.yaml exec ecosystem-security python manage.py check
```

The API is available on `http://127.0.0.1:8120`. See
[container setup](../CONTAINERS.md) for port overrides, required services,
optional profiles, tests and data retention. The commands below run the same
application directly with uv.

## Run

```console
uv venv
uv pip install --python .venv/bin/python -r pyproject.toml --group test -e ../..
uv run --no-sync python manage.py migrate
uv run --no-sync python manage.py createsuperuser
uv run --no-sync uvicorn project.asgi:application --host 127.0.0.1 --port 8120
uv run --no-sync pytest -q
```

Set `EXAMPLE_ALLOWED_HOSTS` when using another hostname. The development key,
SQLite database and HTTP cookies are local settings, not deployment defaults.

## Resources and authentication

| Route | Integration | Contract |
| --- | --- | --- |
| `/articles/?title=example` | django-filter and Guardian | The queryset is filtered by object-level view permission before pagination. Model permissions alone do not disclose objects. |
| `/notes/` | rules | Owner predicates authorize detail actions. The queryset separately restricts list visibility to the owner. |
| `/djoser/token/login/`, `/identity/token/` | Djoser and DRF tokens | Exchange username/password for `auth_token`; send `Authorization: Token <token>`. |
| `/dj-rest-auth/login/`, `/identity/cookie/` | dj-rest-auth and SimpleJWT | Login sets the vendor JWT cookie. Cookie-authenticated writes require an explicit CSRF policy in a deployment. |
| `/_allauth/app/v1/auth/login`, `/identity/allauth/` | allauth headless | Send the returned `meta.session_token` as `X-Session-Token`. |
| `/oauth/`, `/identity/oauth/` | django-oauth-toolkit | The identity endpoint requires an unexpired bearer token with the `read` scope. Register an application in the admin and use the vendor authorization flow. |
| `/schema/`, `/docs/` | drf-spectacular | OpenAPI schema and Swagger UI; documentation visibility is a deployment decision. |
| `/admin/` | Django admin | Manage users and model permissions with Django's normal permission system. |

For a non-superuser, assign the required `demo.*_article` model permissions
through the admin. Grant object permissions through `guardian.shortcuts.assign_perm`
or create an article through the API, which grants its creator view/change/delete
permissions. The source deliberately keeps that business rule in `perform_create`.

```console
curl -u reader:local-password http://127.0.0.1:8120/articles/
curl -u reader:local-password -H 'Content-Type: application/json' \
  -d '{"title":"Example","sensitive_note":"not returned"}' \
  http://127.0.0.1:8120/articles/
```

## Transaction and audit boundaries

`perform_create`, `perform_update` and `perform_destroy` are synchronous hooks.
aiodrf runs them in its thread-sensitive worker. `set_actor` is entered after
DRF authentication, so a token-authenticated user is recorded correctly; Django
middleware alone sees a different authentication stage. The write and permission
assignment use one database transaction. Sensitive content is write-only in the
API, excluded from history and masked in the audit log. Decide retention and
access policies before using either log in production.

The tests use real tokens and ASGI requests. They cover denied modifications,
hidden objects, owner-scoped lists, masked audit data, provider login and CORS.
Production OAuth consent, federation, email delivery and cross-origin cookie
security need to be configured and verified in your deployment. See the [security guidance](../../SECURITY.md).
