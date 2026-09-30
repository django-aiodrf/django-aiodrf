"""Application-owned startup resources and shutdown cleanup."""

from contextlib import asynccontextmanager

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import ConsoleSpanExporter, SimpleSpanProcessor

from aiodrf.utils import run_sync


@asynccontextmanager
async def lifespan():
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    try:
        yield
    finally:
        await run_sync(provider.shutdown)()
