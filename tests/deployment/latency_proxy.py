"""
A TCP proxy that delays each direction by a fixed time, for a database that
is far away: ``--delay 0.002`` adds 4 ms to every round trip. Chunks keep
their order and are pipelined, as with ``tc netem delay``; nothing is
dropped or throttled.

    python -m tests.deployment.latency_proxy --port 0 --target-port 55433 --delay 0.002

It prints the port it listens on, then serves until it is terminated.
"""

import argparse
import asyncio


async def _pipe(reader, writer, delay):
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    async def send():
        while (item := await queue.get()) is not None:
            due, data = item
            await asyncio.sleep(max(0.0, due - loop.time()))
            writer.write(data)
            await writer.drain()
        writer.close()

    sender = asyncio.create_task(send())
    try:
        while data := await reader.read(65536):
            queue.put_nowait((loop.time() + delay, data))
    except ConnectionError:
        pass
    finally:
        queue.put_nowait(None)
        await asyncio.gather(sender, return_exceptions=True)


async def serve(options):
    """Start the proxy; the returned server listens on ``server.sockets[0]``."""

    async def connect(client_reader, client_writer):
        try:
            server_reader, server_writer = await asyncio.open_connection(
                options.target_host, options.target_port
            )
        except OSError:
            client_writer.close()
            return
        await asyncio.gather(
            _pipe(client_reader, server_writer, options.delay),
            _pipe(server_reader, client_writer, options.delay),
        )

    return await asyncio.start_server(connect, "127.0.0.1", options.port)


async def main(options):
    server = await serve(options)
    print(server.sockets[0].getsockname()[1], flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--target-host", default="127.0.0.1")
    parser.add_argument("--target-port", type=int, required=True)
    parser.add_argument(
        "--delay", type=float, required=True, help="seconds, each direction"
    )
    asyncio.run(main(parser.parse_args()))
