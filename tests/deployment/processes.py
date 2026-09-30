"""Own a disposable PostgreSQL database, Uvicorn supervisor and Nginx container.

No system service is restarted. Only the database/container/process created by
this instance is removed. Host networking is Linux-only and listeners bind to
127.0.0.1. Configuration and session secrets stay in the temporary directory.
"""

import asyncio
import contextlib
import hashlib
import json
import math
import os
import platform
import secrets
import signal
import socket
import subprocess
import sys
import textwrap
import time
import uuid
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import httpx
import psycopg
from psycopg import sql

import aiodrf
from tests.live.resource_workload import process_metrics

PROJECT = Path(__file__).resolve().parents[2]


def command(*args, **kwargs):
    try:
        return subprocess.run(
            args, check=True, capture_output=True, text=True, timeout=60, **kwargs
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"{args[0]} failed: {exc.stderr[-4000:]}") from exc


class Deployment:
    def __init__(
        self,
        directory,
        *,
        workers=2,
        pool_size=32,
        image="nginx:stable",
        profile=False,
        upstream_delay=0.005,
        middleware_mode="standard",
        loop="asyncio",
        http="h11",
        http_client="httpx",
        db_backend="django",
        tls=False,
        db_delay=0.0,
    ):
        if not 1 <= workers <= 4 or not 2 <= pool_size <= 64:
            raise ValueError("Invalid worker/pool bounds")
        if profile not in (False, "cpu", "memory"):
            raise ValueError("Profile must be False, 'cpu' or 'memory'")
        if not math.isfinite(upstream_delay) or not 0 <= upstream_delay <= 1:
            raise ValueError("Upstream delay must be between 0 and 1 seconds")
        if middleware_mode not in ("standard", "sync", "inline"):
            raise ValueError("Middleware mode must be 'standard', 'sync' or 'inline'")
        if loop not in ("asyncio", "uvloop") or http not in ("h11", "httptools"):
            raise ValueError(
                "Select an explicit supported Uvicorn loop and HTTP implementation"
            )
        if http_client not in ("httpx", "aiohttp"):
            raise ValueError("HTTP client must be 'httpx' or 'aiohttp'")
        if db_backend not in ("django", "async"):
            raise ValueError("DB backend must be 'django' or 'async'")
        if not math.isfinite(db_delay) or not 0 <= db_delay <= 0.1:
            raise ValueError(
                "DB delay must be between 0 and 0.1 seconds, each direction"
            )
        self.directory = Path(directory).resolve()
        self.workers = workers
        self.pool_size = pool_size
        self.image = image
        self.profile = profile
        self.upstream_delay = upstream_delay
        self.middleware_mode = middleware_mode
        self.loop = loop
        self.http = http
        self.http_client = http_client
        self.db_backend = db_backend
        # Seconds added to each direction between the workers and PostgreSQL
        # (tests/deployment/latency_proxy.py); 0 connects directly.
        self.db_delay = db_delay
        self.db_proxy = None
        # A second Nginx listener with TLS and HTTP/2, self-signed for 127.0.0.1.
        self.tls = tls
        self.tls_url = None
        self.certificate = None
        self.identity = uuid.uuid4().hex
        self.database = "aiodrf_deploy_" + self.identity
        self.container = "aiodrf-deploy-" + self.identity
        self.admin = None
        self.created_database = False
        self.created_container = False
        self.server = None
        self.log = None
        self.environment = {
            **os.environ,
            "PYTHONPATH": str(PROJECT)
            + os.pathsep
            + str(Path(aiodrf.__file__).parent.parent),
            "DJANGO_SETTINGS_MODULE": "tests.deployment.settings",
            "AIODRF_DEPLOYMENT_CONFIG": str(self.directory / "config.json"),
        }

    def start(self):
        try:
            self._database()
            self._server()
            self._proxy()
        except BaseException:
            self.close()
            raise
        return self

    def _database(self):
        self.admin = psycopg.connect(
            dbname="postgres",
            user="bench",
            password="bench",
            host="127.0.0.1",
            port=int(os.environ.get("AIODRF_BENCH_PG_PORT", "55433")),
            autocommit=True,
            connect_timeout=5,
        )
        self.pg_version = self.admin.execute("SHOW server_version").fetchone()[0]
        self.admin.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self.database))
        )
        self.created_database = True
        db_port = self.admin.info.port
        if self.db_delay:
            self.db_proxy = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "tests.deployment.latency_proxy",
                    "--target-port",
                    str(db_port),
                    "--delay",
                    str(self.db_delay),
                ],
                cwd=PROJECT,
                stdout=subprocess.PIPE,
                text=True,
            )
            db_port = int(self.db_proxy.stdout.readline())
        config = {
            "secret": secrets.token_urlsafe(48),
            "directory": str(self.directory),
            "profile": self.profile,
            "upstream_delay": self.upstream_delay,
            "middleware_mode": self.middleware_mode,
            "http_client": self.http_client,
            "db_backend": self.db_backend,
            "db_delay": self.db_delay,
            "database": {
                "ENGINE": (
                    "django_async_backend.db.backends.postgresql"
                    if self.db_backend == "async"
                    else "django.db.backends.postgresql"
                ),
                "NAME": self.database,
                "USER": "bench",
                "PASSWORD": "bench",
                "HOST": "127.0.0.1",
                "PORT": db_port,
                "CONN_MAX_AGE": 0,
                "OPTIONS": {
                    "pool": {"min_size": 1, "max_size": self.pool_size, "timeout": 5}
                },
            },
        }
        with (self.directory / "config.json").open(
            "x", encoding="utf-8"
        ) as destination:
            os.chmod(destination.name, 0o600)
            json.dump(config, destination)
        command(sys.executable, "-m", "tests.deployment.prepare", env=self.environment)
        self.cookies = json.loads((self.directory / "session.json").read_text())

    def _server(self):
        with socket.socket() as listener:
            listener.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            listener.bind(("127.0.0.1", 0))
            listener.listen(256)
            self.direct_url = f"http://127.0.0.1:{listener.getsockname()[1]}"
            self.log = (self.directory / "uvicorn.log").open("x", encoding="utf-8")
            self.server = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "tests.deployment.asgi:application",
                    "--fd",
                    str(listener.fileno()),
                    "--workers",
                    str(self.workers),
                    "--lifespan",
                    "on",
                    "--loop",
                    self.loop,
                    "--http",
                    self.http,
                    "--timeout-graceful-shutdown",
                    "2",
                    "--no-access-log",
                    "--log-level",
                    "warning",
                ],
                cwd=PROJECT,
                env=self.environment,
                pass_fds=(listener.fileno(),),
                start_new_session=True,
                stdout=self.log,
                stderr=subprocess.STDOUT,
            )

    def _proxy(self):
        self.image_id = command(
            "docker", "image", "inspect", "--format", "{{.Id}}", self.image
        ).stdout.strip()
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}"
        tls_server, tls_mounts = self._tls_listener()
        config = textwrap.dedent(f"""
            worker_processes 1;
            pid /tmp/nginx.pid;
            error_log /dev/stderr warn;
            events {{ worker_connections 2048; }}
            http {{
                access_log off;
                client_body_temp_path /tmp/client_body;
                proxy_temp_path /tmp/proxy;
                server {{
                    listen 127.0.0.1:{port};
                    client_max_body_size 16m;
                    proxy_http_version 1.1;
                    proxy_set_header Host 127.0.0.1;
                    proxy_set_header Connection "";
                    proxy_read_timeout 15s;
                    proxy_request_buffering off;
                    location / {{
                        proxy_pass {self.direct_url};
                        proxy_buffering off;
                    }}
                    location /buffered/ {{
                        proxy_pass {self.direct_url}/;
                        proxy_buffering on;
                        proxy_ignore_headers X-Accel-Buffering;
                    }}
                    location /header-buffered/ {{
                        proxy_pass {self.direct_url}/;
                        proxy_buffering on;
                    }}
                    location /compressed/ {{
                        proxy_pass {self.direct_url}/;
                        proxy_buffering off;
                        gzip on;
                        gzip_min_length 0;
                        gzip_types text/event-stream application/x-ndjson;
                    }}
                    location /compressed-buffered/ {{
                        proxy_pass {self.direct_url}/;
                        proxy_buffering on;
                        gzip on;
                        gzip_min_length 0;
                        gzip_types text/event-stream application/x-ndjson;
                    }}
                }}
                {tls_server}
            }}
        """)
        path = self.directory / "nginx.conf"
        with path.open("x", encoding="utf-8") as destination:
            destination.write(config)
        command(
            "docker",
            "run",
            "-d",
            "--pull=never",
            "--name",
            self.container,
            "--network",
            "host",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=16m",  # noqa: S108 -- private container tmpfs
            "--tmpfs",
            "/var/cache/nginx:rw,noexec,nosuid,size=16m",
            "--mount",
            f"type=bind,src={path},dst=/etc/nginx/nginx.conf,readonly",
            *tls_mounts,
            "--entrypoint",
            "nginx",
            self.image_id,
            "-g",
            "daemon off;",
        )
        self.created_container = True
        result = command("docker", "exec", self.container, "nginx", "-v")
        self.nginx_version = result.stderr.strip()

    def _tls_listener(self):
        """The TLS/HTTP/2 server block and its certificate mounts, if enabled."""
        if not self.tls:
            return "", []
        self.certificate = self.directory / "tls.crt"
        key = self.directory / "tls.key"
        command(
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
            "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1",
            "-keyout", str(key), "-out", str(self.certificate),
        )  # fmt: skip
        key.chmod(0o644)  # read by the container's nginx user; a throwaway key
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        self.tls_url = f"https://127.0.0.1:{port}"
        server = f"""
                server {{
                    listen 127.0.0.1:{port} ssl;
                    http2 on;
                    ssl_certificate /etc/nginx/tls.crt;
                    ssl_certificate_key /etc/nginx/tls.key;
                    proxy_http_version 1.1;
                    proxy_set_header Host 127.0.0.1;
                    proxy_set_header Connection "";
                    proxy_read_timeout 15s;
                    location / {{
                        proxy_pass {self.direct_url};
                        proxy_buffering off;
                    }}
                }}"""
        mounts = []
        for name, source in (("tls.crt", self.certificate), ("tls.key", key)):
            mounts += [
                "--mount",
                f"type=bind,src={source},dst=/etc/nginx/{name},readonly",
            ]
        return server, mounts

    async def ready(self):
        async with httpx.AsyncClient(cookies=self.cookies, timeout=1) as client:
            async with asyncio.timeout(30):
                while True:
                    if self.server.poll() is not None:
                        raise RuntimeError(
                            "Uvicorn exited; inspect the test-owned uvicorn.log"
                        )
                    try:
                        response = await client.get(self.url + "/metrics/")
                        if (
                            response.status_code == 200
                            and len(self.active_pids()) == self.workers
                        ):
                            assert response.json()["user"] == "load-test"
                            return
                    except httpx.TransportError:
                        pass
                    await asyncio.sleep(0.05)

    def records(self, phase):
        records = []
        for path in self.directory.glob(f"{phase}-*.json"):
            try:
                records.append(json.loads(path.read_text()))
            except json.JSONDecodeError:
                pass  # A startup/shutdown record may still be being written.
        return records

    def active_pids(self):
        closed = {record["pid"] for record in self.records("closed")}
        return {
            record["pid"]
            for record in self.records("started")
            if record["pid"] not in closed and self.owns_worker(record["pid"])
        }

    def owns_worker(self, pid):
        if self.workers == 1 and pid == self.server.pid:
            return self.server.poll() is None
        try:
            status = Path(f"/proc/{pid}/status").read_text()
        except FileNotFoundError:
            return False
        fields = dict(line.split(":", 1) for line in status.splitlines())
        return (
            int(fields["PPid"].strip()) == self.server.pid
            and "Z" not in fields["State"]
        )

    def kill_worker(self, pid):
        if pid not in self.active_pids():
            raise RuntimeError(
                "Refusing to signal a process not owned by this supervisor"
            )
        os.kill(pid, signal.SIGKILL)

    def metrics(self):
        return {str(pid): process_metrics(pid) for pid in self.active_pids()}

    def database_metrics(self):
        return dict(
            self.admin.execute(
                "SELECT coalesce(state, 'unknown'), count(*) FROM pg_stat_activity "
                "WHERE datname = %s GROUP BY state",
                (self.database,),
            ).fetchall()
        )

    def restart(self):
        if self.server.poll() is not None:
            raise RuntimeError("Cannot restart an exited supervisor")
        self.server.send_signal(signal.SIGHUP)

    def stop_server(self):
        if self.server is not None and self.server.poll() is None:
            started = time.monotonic()
            self.server.terminate()
            try:
                self.server.wait(timeout=15)
            except subprocess.TimeoutExpired:
                # This process group was created with start_new_session=True.
                os.killpg(self.server.pid, signal.SIGKILL)
                self.server.wait(timeout=5)
                raise
            return time.monotonic() - started
        return 0

    def close(self):
        try:
            self.stop_server()
        finally:
            if self.db_proxy is not None:
                self.db_proxy.terminate()
                self.db_proxy.wait(timeout=5)
                self.db_proxy = None
            try:
                if self.created_container:
                    try:
                        logs = command("docker", "logs", self.container)
                        (self.directory / "nginx.log").write_text(
                            logs.stdout + logs.stderr
                        )
                        command("docker", "stop", "--time", "5", self.container)
                    finally:
                        command("docker", "rm", self.container)
                        self.created_container = False
            finally:
                if self.log is not None:
                    self.log.close()
                if self.admin is not None:
                    try:
                        if self.created_database:
                            # Only the unique database whose CREATE succeeded here.
                            self.admin.execute(
                                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                                    sql.Identifier(self.database)
                                )
                            )
                            self.created_database = False
                    finally:
                        self.admin.close()


@contextlib.asynccontextmanager
async def deployment(directory, **kwargs):
    service = Deployment(directory, **kwargs)
    try:
        await _owned_thread(service.start)
        await service.ready()
        yield service
    finally:
        await _owned_thread(service.close)


async def _owned_thread(func):
    # Cancellation cannot stop a running thread. Join before dropping ownership
    # or deleting its temporary directory, including a second parent cancel.
    task = asyncio.create_task(asyncio.to_thread(func))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
    result = task.result()
    if cancelled:
        raise asyncio.CancelledError
    return result


def identity():
    package = Path(aiodrf.__file__).parent
    files = [
        *sorted(
            path
            for path in Path(__file__).parent.glob("*.py")
            if not path.name.startswith("test_")
        ),
        PROJECT / "tests/live/resource_app.py",
        PROJECT / "tests/live/resource_workload.py",
    ]
    optional_versions = {}
    for name in (
        "aiohttp",
        "uvloop",
        "httptools",
        "django-async-backend",
        "psycopg-binary",
    ):
        with contextlib.suppress(PackageNotFoundError):
            optional_versions[name] = version(name)
    return {
        "python": platform.python_version(),
        "gil_enabled": sys._is_gil_enabled(),
        "platform": platform.platform(),
        "cpu_affinity": sorted(os.sched_getaffinity(0)),
        "versions": {
            **optional_versions,
            **{
                name: version(name)
                for name in (
                    "django",
                    "djangorestframework",
                    "asgiref",
                    "uvicorn",
                    "httpx",
                    "psycopg",
                    "psycopg-pool",
                )
            },
        },
        "sha256": {
            **{
                "src/aiodrf/" + str(path.relative_to(package)): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in sorted(package.rglob("*.py"))
            },
            **{
                str(path.relative_to(PROJECT)): hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
                for path in files
            },
        },
    }
