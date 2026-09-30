# Deployment behaviour

aiodrf is tested in a real deployment: several Uvicorn workers behind Nginx,
with PostgreSQL and a psycopg connection pool per worker. This page describes
the behaviour those tests establish, and what you still need to verify in your
own environment.

## Workers and lifespan

Each Uvicorn worker runs its own ASGI lifespan: resources opened by the
[lifespan context manager](lifespan.md), such as HTTP clients, belong to one
worker and its event loop. Workers share only the listening socket.

- **Reloading workers** (`SIGHUP`): Uvicorn replaces its workers while requests
  continue. Each retired worker closes its lifespan resources, including while
  streams are still open.
- **Shutdown** (`SIGTERM`): open streams are closed within Uvicorn's graceful
  shutdown timeout, and every worker releases its resources.
- **A killed worker** (`SIGKILL`): the supervisor starts a replacement and the
  service continues. The killed worker cannot run its cleanup, and its open
  streams end with an end-of-file or a transport error; they are not moved to
  another worker or replayed.

## Database connections

Connection pools are per worker, so the largest number of connections a
deployment can open is the number of workers times the pool size, for each
database. Two workers with pools of four never opened more than eight
connections, even under 64 concurrent authenticated requests.

With Django's connection pool, an authenticated streaming response borrows a
connection only to authenticate the request and returns it before streaming.
Four open streams on a worker with a pool of two did not block the next request.
This was not measured without a pool (persistent or per-request connections):
size and test this with your database backend and authentication.

Keep `CONN_MAX_AGE = 0` under ASGI; see [deployment](deployment.md).

## Streaming through Nginx

Server-Sent Events and NDJSON responses arrive item by item through Nginx when
proxy buffering is off, including with TLS and HTTP/2 between Nginx and the
client. When the client closes a stream (a browser's `EventSource.close()`
sends an HTTP/2 `RST_STREAM`), the producer in the application is closed.
With buffering on, Nginx is allowed to hold small chunks; whether it does
depends on its version and configuration. See
[streaming through Nginx](stream-proxies.md) for the configuration.

Setting `proxy_request_buffering off` does not make Django parse request bodies
as a stream: Django still buffers or spools uploaded bodies.

## Sessions and CSRF

Session-authenticated requests, the rejection of anonymous requests, and CSRF
protection on large JSON and multipart `POST` requests work through Nginx as in
a direct connection.

## What to verify yourself

These tests use a self-signed certificate on a single machine. They do not
cover your production TLS setup, ingress controller, CDN, container platform or
network failures, nor long-running stability under your traffic. Before relying
on long-lived streams or on a connection budget, test your deployed chain of
proxy, workers and database with your own traffic.
