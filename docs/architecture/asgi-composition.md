# ASGI composition for independent endpoints

An endpoint that only aggregates asynchronous HTTP calls or accesses Redis/Kafka
does not necessarily need DRF serializers, permissions or Django middleware.
The appropriate boundary depends on which guarantees the application needs.
This document evaluates that boundary; aiodrf does not currently expose a
separate raw-route decorator or promise middleware-free Django views.

## Execution boundaries

| Endpoint implementation | DRF processing | Django request lifecycle | Application responsibilities |
| --- | --- | --- | --- |
| aiodrf APIView | Retained | Retained | Usual Django/DRF configuration |
| Plain async Django view returning HttpResponse | Removed | Retained | Explicit validation and response handling; Django middleware still applies |
| Independent ASGI application selected before Django | Removed | Removed for matching requests | All HTTP policy, authentication, resource and protocol contracts |
| Separate ASGI service behind the proxy | Removed | Removed | Same contracts, plus deployment and service boundaries |

Disabling authentication classes, using a faster response encoder or subclassing
APIView does not bypass Django's handler. A normal async Django view still
participates in middleware, request construction, signals and response cleanup.
Django also adapts synchronous middleware when required; see
[Django's async documentation](https://docs.djangoproject.com/en/6.1/topics/async/).

To avoid that lifecycle entirely, route before calling Django's ASGI application:

```text
ASGI server
  -> outer HTTP policy and application dispatch
       -> explicitly mounted independent application
       -> Django ASGI application -> Django middleware -> Django/aiodrf views
```

This is application composition, not an optimization that can preserve every
Django/DRF guarantee automatically. A raw function still pays ASGI server,
socket, HTTP parsing, selected middleware and its own encoding costs.

## Recommended integration boundary

Start with a plain Django async view if Django security/middleware is still
required. For genuinely independent endpoints, prefer an established ASGI
application/router mounted outside Django, or a proxy-level split. Do not add
a parallel request/response framework merely to remove a small dispatch cost.

If a library helper is justified after measurement, keep it optional under
contrib and limited to mounting ASGI callables. It should leave Django's URLconf,
routers, views and global settings unchanged. A new `view` decorator, custom
request type, dependency injector and schema generator would materially expand
the maintenance surface and are not recommended for this requirement.

The existing `aiodrf.asgi.LifespanApplication` accepts an ASGI application and
owns lifespan processing; it is not an HTTP router. A composed application must
have one explicit lifespan owner. Mounting child applications does not by itself
prove that their startup/shutdown handlers execute. Initialize HTTP clients,
Redis pools and Kafka producers in the appropriate worker/event loop and close
them on shutdown. The [ASGI lifespan specification](https://asgi.readthedocs.io/en/latest/specs/lifespan.html)
defines the connection between lifespan, event loops and per-request state.

## Security and compatibility requirements

Independent routes do not inherit Django authentication, sessions, CSRF, CORS,
host validation, security headers, throttles, DRF exceptions or OpenAPI discovery.
Session cookies sent by a browser do not establish a verified user on this path.
Specify equivalent controls or explicitly document why a control does not apply.

Before adopting an outer router, require:

- Exact path/prefix precedence, `root_path` handling and a default Django fallback.
  A path prefix must not accidentally capture additional Django endpoints.
- Explicit methods, HEAD/OPTIONS behavior and consistent 404/405 responses.
- Request-body/header limits, overload handling, upstream timeouts and bounded
  aggregation fan-out. External URLs must not be derived from untrusted input
  without an SSRF policy.
- Trusted-proxy/host policy, TLS handling, authentication and authorization;
  security controls must run before either branch where applicable.
- Streaming backpressure, disconnect cancellation and cleanup of partially
  completed upstream work. Kafka/Redis delivery semantics remain client/service
  contracts, not router guarantees.
- Separate or deliberately combined schema and URL documentation. Django
  `reverse()` and drf-spectacular cannot discover arbitrary ASGI mounts.
- No accidental ORM use on the event loop. A bypassed Django lifecycle does not
  establish Django's connection cleanup or transaction boundaries.

## Acceptance tests and measurement

Compare the same aggregation handler and payload through aiodrf, a plain Django
view and an outer ASGI mount under identical server, workers and upstream delays.
Verify response/policy equivalence first. Measure throughput, tail latency,
event-loop delay, allocations and cancellation, not only a constant JSON response.

Test routing collisions, encoded paths, method handling, root-path deployments,
lifespan startup failure, shutdown ordering, isolated client pools, body limits,
disconnects, overload rejection and security failures. Prove that a matched raw
request does not call Django and that an unmatched request calls Django exactly
once. Existing endpoints must remain unchanged with the helper disabled.

No raw-route public API is implemented by this evaluation. The smallest potential
addition is an optional ASGI mounting helper with these contracts, subject to
separate feature approval and measured benefit.
