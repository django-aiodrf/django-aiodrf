"""Prefork and broker reconnection with owned processes, never shared Redis restarts."""

import asyncio
import gc
import os
import shutil
import signal
import socket
import subprocess
import sys
import uuid

import redis
from celery import Celery

from aiodrf.utils import run_sync


def docker(*args):
    executable = shutil.which("docker")
    assert executable is not None, (
        "Docker is required by this explicit integration test."
    )
    return subprocess.run(
        [executable, *args],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    ).stdout.strip()


def stop_worker(worker):
    if worker.poll() is None:
        worker.terminate()
        try:
            worker.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(
                worker.pid, signal.SIGKILL
            )  # This test created the process group.
            worker.wait(timeout=5)
            raise


async def test_prefork_duplicate_effect_and_broker_recovery(tmp_path):  # noqa: PLR0915 -- ownership and recovery in one scenario
    name = "aiodrf-celery-" + uuid.uuid4().hex
    image = await asyncio.to_thread(
        docker, "image", "inspect", "--format", "{{.Id}}", "redis:7-alpine"
    )
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    container = await asyncio.to_thread(
        docker,
        "run",
        "-d",
        "--pull=never",
        "--name",
        name,
        "--read-only",
        "--tmpfs",
        "/data:rw,noexec,nosuid,size=16m",
        "-p",
        f"127.0.0.1:{port}:6379",
        image,
        "redis-server",
        "--save",
        "",
        "--appendonly",
        "no",
    )
    worker, app, client = None, None, None
    try:
        address = await asyncio.to_thread(docker, "port", container, "6379/tcp")
        assert address.startswith("127.0.0.1:")
        url = "redis://" + address + "/0"
        client = redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=2)
        ready = tmp_path / "ready"
        log_path = tmp_path / "worker.log"
        with log_path.open("x") as log:
            worker = await asyncio.to_thread(
                subprocess.Popen,
                [
                    sys.executable,
                    "-m",
                    "celery",
                    "-A",
                    "tests.integrations.celery_prefork_app:app",
                    "worker",
                    "--pool=prefork",
                    "--concurrency=2",
                    "--without-gossip",
                    "--without-mingle",
                    "--without-heartbeat",
                    "--loglevel=INFO",
                ],
                env={
                    **os.environ,
                    "TEST_CELERY_URL": url,
                    "TEST_CELERY_READY": str(ready),
                },
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            async with asyncio.timeout(15):
                while not ready.exists():
                    assert worker.poll() is None, log_path.read_text()
                    await asyncio.sleep(0.05)
            app = Celery(
                "aiodrf-prefork-publisher",
                broker=url,
                backend=url,
                set_as_current=False,
            )
            app.conf.update(
                task_default_queue="aiodrf-prefork-contract", result_expires=60
            )

            async def execute(key):
                result = await run_sync(app.send_task)(
                    "aiodrf.contract.idempotent_effect",
                    args=[key],
                    retry=False,
                )
                try:
                    return await run_sync(result.get)(timeout=10)
                finally:
                    await run_sync(result.forget)()

            results = await asyncio.gather(execute("duplicate"), execute("duplicate"))
            assert sorted(result["applied"] for result in results) == [False, True]
            assert all(
                result["pid"] not in (os.getpid(), worker.pid) for result in results
            )
            # AsyncResult finalizers may unsubscribe; exercise them while the
            # test-owned result backend is still available, not at pytest exit.
            await asyncio.to_thread(gc.collect)
            # An actual outage, observed by the worker before starting Redis again.
            await asyncio.to_thread(docker, "stop", "--time", "2", container)
            async with asyncio.timeout(10):
                while "Connection to broker lost" not in log_path.read_text():
                    assert worker.poll() is None, log_path.read_text()
                    await asyncio.sleep(0.05)
            await asyncio.to_thread(docker, "start", container)
            async with asyncio.timeout(10):
                while True:
                    try:
                        if await asyncio.to_thread(client.ping):
                            break
                    except redis.ConnectionError:
                        pass
                    await asyncio.sleep(0.05)
            assert (await execute("after-recovery"))["applied"]
            assert worker.poll() is None
    finally:
        try:
            if worker is not None:
                await asyncio.to_thread(stop_worker, worker)
        finally:
            try:
                if app is not None:
                    await asyncio.to_thread(gc.collect)
                    app.producer_pool.force_close_all()
                    app.pool.force_close_all()
                    app.backend.result_consumer.stop()
                    app.backend.client.connection_pool.disconnect()
                    app.close()
                if client is not None:
                    client.close()
            finally:
                await asyncio.to_thread(docker, "rm", "-f", container)
