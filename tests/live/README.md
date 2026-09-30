# Live HTTP regression tests

Run `nox -s live resources` explicitly, or `live_postgres` with the dedicated
PostgreSQL service. These tests verify real HTTP responses, stream cancellation,
connection cleanup and bounded resource observations. Ordinary unit tests do not
start these servers. Timing observations diagnose failures; they are not a release
performance gate or a production capacity estimate.

The SQLite live session uses `tests.settings_live` and a disposable file through
`AIODRF_LIVE_SQLITE`. Do not run the real-server contract with the ordinary in-memory
unit-test database: Django deliberately keeps in-memory connections open, which is
not the connection lifecycle this test measures. The nox session configures this.

Run `nox -s asgi_servers` for the Uvicorn and Granian process-level contract.
Each server gets a temporary SQLite database, an isolated loopback listener and
an application-owned HTTP client. The tests cover database responses, concurrent
requests, parsing, stream disconnection and lifespan shutdown. They terminate
only the processes they start. This profile uses Python 3.14 on POSIX and the
versions in `requirements/servers/requirements.txt`; it does not establish
Windows or free-threaded server compatibility.
