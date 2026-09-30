"""Explicit async indexing of django-opensearch-dsl documents.

Document preparation may read Django relations and therefore stays in a worker.
HTTP and bulk operations use the application's lifespan-owned AsyncOpenSearch.
The writer does not install signals, own a client or change vendor registries.
"""

from collections.abc import AsyncGenerator, AsyncIterable
from contextlib import aclosing
from typing import Any, Literal

from django.db.models import Model, QuerySet
from django_opensearch_dsl import Document
from opensearchpy import AsyncOpenSearch
from opensearchpy.helpers import async_bulk

from aiodrf.utils import run_sync

__all__ = ["AsyncDocumentWriter"]


class AsyncDocumentWriter:
    """Index a registered Document class with explicit index and client ownership.

    Model IDs, field preparation and ``should_index_object()`` remain the
    document's responsibility. The writer deliberately does not emulate indexing
    signals, related-model propagation or database/search atomic transactions.
    """

    def __init__(
        self, document: type[Document], *, client: AsyncOpenSearch, index: str
    ) -> None:
        self.document = document
        self.client = client
        self.index = index

    def _action(self, instance: Model, action: str) -> dict[str, Any] | None:
        # Keep mutable preparation state per operation, not on a shared writer.
        document = self.document()
        if action != "delete" and not document.should_index_object(instance):
            return None
        data = {
            "_op_type": action,
            "_index": self.index,
            "_id": document.generate_id(instance),
        }
        if data["_id"] is None:
            raise ValueError(
                "Save the model or supply a document.generate_id() before indexing."
            )
        if action != "delete":
            data["_source"] = document.prepare(instance)
        return data

    async def aindex(self, instance: Model, **kwargs: Any) -> Any:
        """Return the native index response, or None for an excluded model."""
        action = await run_sync(self._action)(instance, "index")
        if action is None:
            return None
        return await self.client.index(
            index=self.index, id=action["_id"], body=action["_source"], **kwargs
        )

    async def adelete(self, instance: Model, **kwargs: Any) -> Any:
        """Delete by the document's model ID; preparation is not evaluated."""
        action = await run_sync(self._action)(instance, "delete")
        if action is None:
            raise AssertionError("Delete actions cannot be excluded.")
        return await self.client.delete(index=self.index, id=action["_id"], **kwargs)

    async def abulk(
        self,
        instances: AsyncIterable[Model],
        *,
        action: Literal["index", "create", "delete"] = "index",
        **kwargs: Any,
    ) -> Any:
        """Pass bounded actions to opensearch-py's async_bulk helper.

        Supply a QuerySet.aiterator() or another async iterable. A synchronous
        QuerySet/generator is rejected so iteration cannot block the event loop.
        Vendor bulk options and errors are passed through unchanged.
        """
        if action not in {"index", "create", "delete"}:
            raise ValueError("action must be index, create or delete.")
        if isinstance(instances, QuerySet) or not isinstance(instances, AsyncIterable):
            raise TypeError(
                "abulk requires an async iterable; use QuerySet.aiterator()."
            )

        async def actions() -> AsyncGenerator[dict[str, Any], None]:
            iterator = aiter(instances)
            try:
                async for instance in iterator:
                    data = await run_sync(self._action)(instance, action)
                    if data is not None:
                        yield data
            finally:
                # ``async for`` does not close what it iterates: a failed or
                # cancelled bulk would leave a QuerySet.aiterator()'s cursor
                # open until garbage collection.
                close = getattr(iterator, "aclose", None)
                if close is not None:
                    await close()

        async with aclosing(actions()) as stream:
            return await async_bulk(self.client, stream, **kwargs)
