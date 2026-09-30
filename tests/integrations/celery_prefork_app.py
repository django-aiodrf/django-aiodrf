"""Disposable worker application for the real broker-recovery contract."""

import os
from pathlib import Path

import redis
from celery import Celery
from celery.signals import worker_ready

app = Celery(
    "aiodrf-prefork-contract",
    broker=os.environ["TEST_CELERY_URL"],
    backend=os.environ["TEST_CELERY_URL"],
)
app.conf.update(
    task_default_queue="aiodrf-prefork-contract",
    broker_connection_retry_on_startup=True,
    broker_connection_retry=True,
    broker_connection_max_retries=5,
    broker_transport_options={"socket_connect_timeout": 1, "socket_timeout": 2},
    worker_enable_remote_control=False,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    result_expires=60,
)


@worker_ready.connect
def ready(**kwargs):
    Path(os.environ["TEST_CELERY_READY"]).touch()


@app.task(name="aiodrf.contract.idempotent_effect")
def idempotent_effect(key):
    # The application's effect *is* this atomic insertion. This does not claim
    # exactly-once delivery or cover a separate DB/API side effect after SET NX.
    with redis.Redis.from_url(
        os.environ["TEST_CELERY_URL"], socket_timeout=2
    ) as client:
        applied = client.set("effect:" + key, "applied", nx=True)
    return {"applied": bool(applied), "pid": os.getpid()}
