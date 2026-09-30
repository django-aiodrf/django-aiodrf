# Deployment tests

A disposable Django project that runs aiodrf behind real Uvicorn workers,
Nginx and PostgreSQL, to test resource ownership, worker replacement,
authentication, streaming and connection pool limits. The behaviour these
tests establish is summarized in the
[deployment behaviour guide](../../docs/guides/deployment-validation.md).
Run them only against dedicated test services.

## Prerequisites

- Linux with Docker, and a local Nginx image (`nginx:stable` by default). The
  harness resolves the image to its local ID and runs it with `--pull=never`,
  so it never downloads a different Nginx version.
- The PostgreSQL service of `tests/services/compose.yaml`, on `127.0.0.1:55433`
  by default; set `AIODRF_BENCH_PG_PORT` to use another port.
- The development and integration dependencies:
  `uv pip install -e . --group dev --group integration`. The HTTP/2 test needs
  `h2`, installed by the `integration` group through `httpx[http2]`.

Each run creates, migrates and finally drops its own `aiodrf_deploy_<uuid>`
database; no other database is touched. Workers use Django's PostgreSQL backend
with `CONN_MAX_AGE = 0` and a psycopg pool per process. Test sessions belong to
a synthetic user, and secrets and cookies stay in the temporary run directory.

The harness owns one Uvicorn process group and one uniquely named Nginx
container with a read-only file system, both listening on `127.0.0.1` (Docker
host networking is Linux-specific). Cleanup removes exactly these resources,
also when a test fails. If the harness itself is killed with `SIGKILL`, note
the unique resource names before cleaning up by hand, and never remove
containers or databases by a name prefix.

## Running the tests

```console
.venv/bin/pytest -q -s tests/deployment/test_server.py tests/deployment/test_harness.py \
    tests/deployment/test_stream_proxy.py tests/deployment/test_connection_budget.py
```

`test_harness.py` tests the harness itself, needs no service and also runs in
`nox -s tests`.

## Long runs

A short run:

```console
.venv/bin/python -m tests.deployment.soak --seconds 120 --cycle-seconds 10 --streams 16 --output /tmp/aiodrf-soak-short.jsonl
```

The same workload for two hours:

```console
.venv/bin/python -m tests.deployment.soak --seconds 7200 --cycle-seconds 30 --streams 16 --output /tmp/aiodrf-soak-two-hours.jsonl
```

Each cycle opens a group of authenticated Server-Sent Events connections: half
read slowly and half stop reading after the first event, with 4 KiB of padding
per event to keep slow consumers under pressure. In parallel, equivalent DRF and
aiodrf list requests and calls to a local HTTP service (5 ms latency) run at a
concurrency of eight, and every response is checked. At the end of each cycle
all streams are closed, and the run records, per worker, the resident memory,
threads and open files, and the state of the PostgreSQL connections.

The output is in JSON Lines: a header, one record per cycle, and a `complete`
record written only after shutdown and cleanup, so a file without it belongs to
an interrupted run. Output files are never overwritten. Each cycle keeps the
first 32 request samples as examples, and each worker keeps its last 1,000
event-loop lag observations.

Thread and file counts must return to their baseline after each cycle. Memory
is recorded for comparison, not checked against a fixed limit: compare settled
cycles after the warm-up rather than the first and the last. The run does not
force garbage collection. The duration covers the cycles, not start-up and
shutdown, so the last cycle can end slightly later than requested.
