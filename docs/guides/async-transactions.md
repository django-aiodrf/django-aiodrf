# Working with async views and transactions

Use async handlers for awaitable work, but keep a database transaction in a
single synchronous function when using Django's standard ORM. The async ORM
methods adapt sync operations; they do not make `atomic()` an async context
manager. See [Django transactions](https://docs.djangoproject.com/en/6.0/topics/db/transactions/)
and the [limitations reference](../reference/limitations.md).

## One transactional operation, one worker boundary

```python
from asgiref.sync import sync_to_async
from django.db import transaction
from django.db.models import F


def reserve_stock(product_id: int, quantity: int) -> int:
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    with transaction.atomic():
        product = Product.objects.select_for_update().get(pk=product_id)
        if product.available < quantity:
            raise InsufficientStock(product_id)
        Product.objects.filter(pk=product_id).update(
            available=F("available") - quantity
        )
        reservation = Reservation.objects.create(
            product_id=product_id, quantity=quantity
        )
        return reservation.pk


async def reserve(product_id: int, quantity: int) -> int:
    return await sync_to_async(reserve_stock, thread_sensitive=True)(
        product_id, quantity
    )
```

The model and domain exception names above belong to the application. Return
an identifier or fully materialized values, not a lazy queryset whose later
evaluation leaves the transaction. Do not use `thread_sensitive=False` merely
to distribute database calls across more threads.

## Atomic writes without a read/modify/write race

A single conditional `aupdate()` with `F()` can express many counters and
inventory changes without a surrounding application transaction. Check the
affected-row count. For a multi-step invariant or row lock, use the synchronous
unit above. Concurrent sync requests can lose updates too; this is not a race
introduced by the async syntax itself.

## ATOMIC_REQUESTS and ATOMIC_SAVE

Disable `ATOMIC_REQUESTS` for the database used by async views. It cannot wrap
an async Django view. aiodrf's `ATOMIC_SAVE=True` wraps its eligible default
synchronous model save, including ordinary many-to-many writes. It does not
make permission checks, representation, remote calls or arbitrary overridden
save hooks part of the same transaction. Choose an explicit synchronous
`perform_create`/`perform_update` operation when the application owns a larger
transactional invariant. Avoid nested transactions solely because both layers
can open one; define which layer owns the unit of work.

## External I/O and commit callbacks

Do not hold a row lock while awaiting a remote API. Gather external data before
opening the transaction, then recheck database invariants inside it. Publish
notifications after commit using `transaction.on_commit`. The callback is
synchronous: a blocking Celery publish belongs in the worker, not on the event
loop. A callback failure occurs after commit and cannot roll back the data.
Use a transactional outbox if publication must survive process/broker failure.

## Cancellation, connections and tests

Client cancellation does not terminate the database thread. Use idempotent
operations and database constraints, not an assumption that a cancelled await
means no write occurred. Configure connection and statement timeouts at the
appropriate driver/database layer. Keep `CONN_MAX_AGE=0` for ASGI and size pools
against all workers, replicas and aliases. `close_old_connections()` is not a
general-purpose async pool-return operation.

Use PostgreSQL transaction tests for locking, rollback and competing updates.
SQLite cannot establish PostgreSQL lock semantics. Tests needing independent
connections should use `pytest.mark.django_db(transaction=True)` and close their
worker-owned connections before teardown. Keep native backend transaction APIs
in their explicit contrib integration; do not mix a synchronous connection's
transaction with a different async driver's connection.
