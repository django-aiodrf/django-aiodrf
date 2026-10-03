"""Cross sync broker/storage boundaries explicitly; native search remains awaited."""

from functools import partial
from uuid import uuid4

from aiodrf_asgi_lifespan.asgi import get_lifespan_state
from django.conf import settings
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import transaction
from django.utils.cache import patch_vary_headers
from elasticsearch.dsl import AsyncSearch
from rest_framework import serializers
from rest_framework.exceptions import APIException

from aiodrf import aio
from aiodrf.response import Response
from aiodrf.utils import run_sync
from aiodrf.views import APIView
from aiodrf.viewsets import ModelViewSet

from .lifecycle import Resources
from .models import Job
from .search import SearchItem
from .tasks import complete_job


class JobSerializer(serializers.ModelSerializer):
    class Meta:
        model = Job
        fields = ["id", "title", "completed"]
        read_only_fields = ["completed"]


class Jobs(ModelViewSet):
    queryset = Job.objects.order_by("pk")
    serializer_class = JobSerializer

    def perform_create(self, serializer):
        with transaction.atomic():
            job = serializer.save()
            # A committed record may outlive a failed broker publish. Use an
            # outbox in applications that require guaranteed delivery.
            transaction.on_commit(partial(complete_job.delay, job.pk))


class TextUpload(serializers.Serializer):
    text = serializers.CharField(max_length=4096)


class Storage(APIView):
    async def post(self, request):
        serializer = TextUpload(data=await request.adata())
        await aio.is_valid(serializer, raise_exception=True)
        with ContentFile(serializer.validated_data["text"].encode()) as content:
            name = await run_sync(default_storage.save)(
                f"notes/{uuid4().hex}.txt", content
            )
        return Response({"name": name}, status=201)


class Search(APIView):
    async def get(self, request):
        resources = get_lifespan_state(request, Resources)
        if resources.search is None:
            exc = APIException("Search is not configured")
            exc.status_code = 503
            raise exc
        query = AsyncSearch(using=resources.search, index=settings.EXAMPLE_SEARCH_INDEX)
        query = query.query("match", title=request.query_params.get("q", ""))[:20]
        result = await query.execute()
        return Response({"titles": [hit.title for hit in result]})

    async def post(self, request):
        resources = get_lifespan_state(request, Resources)
        if resources.search is None:
            raise SearchUnavailable()
        serializer = SearchInput(data=await request.adata())
        await aio.is_valid(serializer, raise_exception=True)
        identifier = uuid4().hex
        document = SearchItem(meta={"id": identifier}, **serializer.validated_data)
        await document.save(
            using=resources.search,
            index=settings.EXAMPLE_SEARCH_INDEX,
            refresh="wait_for",
        )
        return Response({"id": identifier}, status=201)


class SearchUnavailable(APIException):
    status_code = 503
    default_detail = "Search is not configured"


class SearchInput(serializers.Serializer):
    title = serializers.CharField(max_length=120)
    completed = serializers.BooleanField(default=False)


class DjangoSearch(APIView):
    """Use the synchronous Django Document API in aiodrf's worker thread."""

    def get(self, request):
        if not settings.EXAMPLE_SEARCH_URL:
            raise SearchUnavailable()
        from .documents import JobDocument

        result = (
            JobDocument.search()
            .query("match", title=request.query_params.get("q", ""))[:20]
            .execute()
        )
        return Response({"titles": [hit.title for hit in result]})

    def post(self, request):
        if not settings.EXAMPLE_SEARCH_URL:
            raise SearchUnavailable()
        from django.shortcuts import get_object_or_404

        from .documents import JobDocument

        serializer = JobIndexInput(data=request.data)
        serializer.is_valid(raise_exception=True)
        job = get_object_or_404(Job, pk=serializer.validated_data["job_id"])
        JobDocument().update(job, refresh="wait_for")
        return Response({"indexed": job.pk})


class JobIndexInput(serializers.Serializer):
    job_id = serializers.IntegerField(min_value=1)


class CachedValue(APIView):
    async def get(self, request):
        cache = self.get_cache(request)
        return Response({"value": await cache.aget("value")})

    async def post(self, request):
        cache = self.get_cache(request)
        serializer = SearchInput(data=await request.adata())
        await aio.is_valid(serializer, raise_exception=True)
        await cache.aset("value", dict(serializer.validated_data), timeout=30)
        return Response({"stored": True})

    def get_cache(self, request):
        cache = get_lifespan_state(request, Resources).cache
        if cache is None:
            error = APIException("Async cache is not configured")
            error.status_code = 503
            raise error
        return cache


class CachedPage(APIView):
    """A public response whose token identifies each actual view execution."""

    async def get(self, request):
        response = Response(
            {"token": uuid4().hex, "tenant": request.headers.get("X-Tenant", "public")}
        )
        patch_vary_headers(response, ["X-Tenant"])
        return response


class OpenSearchJobs(APIView):
    """Explicit Django document writes and native OpenSearch DSL reads."""

    def get_search_client(self, request):
        client = get_lifespan_state(request, Resources).opensearch
        if client is None:
            raise SearchUnavailable()
        return client

    async def get(self, request):
        from opensearchpy import Search as Query

        client = self.get_search_client(request)
        query = Query().query("match", title=request.query_params.get("q", ""))[:20]
        result = await client.search(
            index=settings.EXAMPLE_OPENSEARCH_INDEX, body=query.to_dict()
        )
        return Response(
            {"titles": [hit["_source"]["title"] for hit in result["hits"]["hits"]]}
        )

    async def post(self, request):
        from django.shortcuts import aget_object_or_404

        from aiodrf.contrib.opensearch import AsyncDocumentWriter

        from .opensearch_documents import JobOpenSearchDocument

        client = self.get_search_client(request)
        serializer = JobIndexInput(data=await request.adata())
        await aio.is_valid(serializer, raise_exception=True)
        job = await aget_object_or_404(Job, pk=serializer.validated_data["job_id"])
        writer = AsyncDocumentWriter(
            JobOpenSearchDocument,
            client=client,
            index=settings.EXAMPLE_OPENSEARCH_INDEX,
        )
        await writer.aindex(job, refresh="wait_for")
        return Response({"id": str(job.pk)}, status=201)
