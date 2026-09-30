"""Granian and Uvicorn contracts through real sockets and process shutdown."""

import asyncio
import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="Owned POSIX process group cleanup"
)
PROJECT = Path(__file__).resolve().parents[2]


@pytest.fixture(params=["uvicorn", "granian"])
async def server(request, tmp_path):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    target = "tests.live.server_app:create_application"
    args = [
        sys.executable,
        "-m",
        request.param,
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--workers",
        "1",
    ]
    args += (
        ["--interface", "asgi", "--loop", "asyncio"]
        if request.param == "granian"
        else ["--lifespan", "on", "--loop", "asyncio"]
    )
    journal = tmp_path / "shutdown.json"
    environment = {
        **os.environ,
        "PYTHONPATH": str(PROJECT) + os.pathsep + str(PROJECT / "src"),
        "AIODRF_SERVER_DATABASE": str(tmp_path / "server.sqlite3"),
        "AIODRF_SERVER_JOURNAL": str(journal),
    }
    log_path = tmp_path / "server.log"
    await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "tests.live.server_app"],
        cwd=PROJECT,
        env=environment,
        check=True,
        capture_output=True,
        timeout=20,
    )
    with log_path.open("w") as log:
        process = await asyncio.create_subprocess_exec(
            *args,
            target,
            cwd=PROJECT,
            env=environment,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            async with httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}", trust_env=False, timeout=5
            ) as client:
                async with asyncio.timeout(20):
                    while True:
                        assert process.returncode is None, log_path.read_text()
                        try:
                            ready = await client.get("/metrics/")
                            if ready.status_code == 200:
                                break
                        except httpx.TransportError:
                            pass
                        await asyncio.sleep(0.05)
                yield client
        finally:
            if process.returncode is None:
                os.killpg(process.pid, signal.SIGTERM)
            try:
                await asyncio.wait_for(process.wait(), timeout=15)
            except TimeoutError:
                os.killpg(process.pid, signal.SIGKILL)
                await asyncio.wait_for(process.wait(), timeout=5)
                pytest.fail("Server exceeded its graceful shutdown budget")
        assert process.returncode in (0, -signal.SIGTERM), log_path.read_text()
        assert journal.is_file(), log_path.read_text()
        closed = json.loads(journal.read_text())
        assert closed["opened"] == closed["closed"]
        assert closed["background_done"]
        assert closed["client_closed"]


async def test_database_and_external_io_with_lifespan(server):
    reference = await server.get("/drf/")
    response = await server.get("/aiodrf/")
    assert reference.status_code == response.status_code == 200
    assert response.json() == reference.json()
    assert len(response.json()) == 30
    results = await asyncio.gather(*(server.get("/external/") for _ in range(4)))
    assert all(
        response.status_code == 200 and response.json() == {"body": "ok"}
        for response in results
    )
    assert (await server.get("/missing/")).status_code == 404


async def test_request_bodies_and_stream_disconnect(server):
    response = await server.post("/payload/", json={"text": "example"})
    assert response.status_code == 200
    assert response.json() == {"size": 7}
    upload = await server.post(
        "/payload/", files={"file": ("example.txt", b"uploaded")}
    )
    assert upload.status_code == 200
    assert upload.json() == {"size": 8}
    invalid = await server.post(
        "/payload/", content=b"{", headers={"Content-Type": "application/json"}
    )
    assert invalid.status_code == 400
    async with server.stream("GET", "/stream/") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        async with contextlib.aclosing(response.aiter_lines()) as lines:
            async with asyncio.timeout(5):
                async for line in lines:
                    if line.startswith("data:"):
                        assert json.loads(line.removeprefix("data:")) == {"ready": True}
                        break
                else:
                    pytest.fail("Stream ended before its first event")
    async with asyncio.timeout(5):
        while True:
            state = (await server.get("/metrics/")).json()
            if state["opened"] == state["closed"] == 1:
                break
            await asyncio.sleep(0.05)
