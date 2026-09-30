"""A consumer under ``mypy --strict``, checked by the typecheck session, not executed."""

from typing import assert_type

from django.db.models import QuerySet

from aiodrf import aio, generics, serializers, viewsets
from aiodrf.request import Request
from aiodrf.response import Response
from tests.testapp.models import Author


class AuthorSerializer(serializers.ModelSerializer[Author]):
    class Meta:
        model = Author
        fields = ["name"]


class AuthorDetail(generics.RetrieveUpdateAPIView[Author]):
    queryset = Author.objects.all()
    serializer_class = AuthorSerializer

    # An async ``get`` conflicts with DRF's synchronous signature, as in
    # aiodrf itself; what it would call is typed on its own.
    async def show(self, request: Request) -> Response:
        author = await self.aget_object()
        assert_type(author, Author)
        assert_type(await self.aget_queryset(), QuerySet[Author])
        serializer = AuthorSerializer(author, data=await request.adata())
        assert_type(await aio.is_valid(serializer, raise_exception=True), bool)
        # The model type reaches ``asave()``; DRF's ModelSerializer, which
        # aio.save() takes, is not generic at runtime.
        assert_type(await serializer.asave(), Author)
        await aio.save(serializer)
        return Response(await aio.data(serializer))


class Authors(viewsets.ModelViewSet[Author]):
    queryset = Author.objects.all()
    serializer_class = AuthorSerializer

    async def aget_queryset(self) -> QuerySet[Author]:
        return (await super().aget_queryset()).filter(name__startswith="a")


async def represent(author: Author) -> object:
    return await AuthorSerializer(author).adata()
