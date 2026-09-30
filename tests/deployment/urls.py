from importlib.util import find_spec

from django.conf import settings
from django.urls import path

from aiodrf.response import StreamingResponse
from tests.deployment import views

urlpatterns = [
    path("drf/", views.DRFAuthors.as_view()),
    path("aiodrf/", views.Authors.as_view()),
    path("drf-detail/<int:pk>/", views.DRFAuthorDetail.as_view()),
    path("aiodrf-detail/<int:pk>/", views.AuthorDetail.as_view()),
    path("drf-read/", views.DRFRead.as_view()),
    path("aiodrf-read/", views.AsyncRead.as_view()),
    path("drf-read/<int:pk>/", views.DRFRead.as_view()),
    path("aiodrf-read/<int:pk>/", views.AsyncRead.as_view()),
    path("external/", views.External.as_view()),
    path("drf-external/", views.DRFExternal.as_view()),
    path(
        "public-external/",
        views.External.as_view(authentication_classes=[], permission_classes=[]),
    ),
    path(
        "public-drf-external/",
        views.DRFExternal.as_view(authentication_classes=[], permission_classes=[]),
    ),
    path("django-external/", views.django_external),
    path("nested/", views.NestedJSON.as_view()),
    path("drf-nested/", views.DRFNestedJSON.as_view()),
    path("stream/", views.Stream.as_view()),
    path("ndjson/", views.Stream.as_view(response_class=StreamingResponse)),
    path("metrics/", views.Metrics.as_view()),
    path("payload/", views.Payload.as_view()),
    path("large/", views.LargeJSON.as_view()),
    path("large-thread/", views.ThreadedLargeJSON.as_view()),
    path("large-msgspec/", views.FastLargeJSON.as_view()),
]
if find_spec("aiodrf.contrib.builtin.concurrent") is not None:
    # Older archived source trees remain usable by the existing comparison harness.
    from tests.deployment.concurrent_views import (
        BulkPrefetched,
        ConcurrentEnrichment,
        GatherPrefetched,
        PairedExternalItem,
        SequentialEnrichment,
    )

    urlpatterns += [
        path("sequential-items/", SequentialEnrichment.as_view()),
        path("concurrent-items/", ConcurrentEnrichment.as_view()),
        path(
            "paired-items/",
            ConcurrentEnrichment.as_view(serializer_class=PairedExternalItem),
        ),
        path(
            "prefetch-bulk-items/",
            SequentialEnrichment.as_view(serializer_class=BulkPrefetched),
        ),
        path(
            "prefetch-gather-items/",
            SequentialEnrichment.as_view(serializer_class=GatherPrefetched),
        ),
    ]
if settings.CONFIG.get("db_backend") == "async":
    urlpatterns += [
        path("native-read/", views.NativeRead.as_view()),
        path("native-read/<int:pk>/", views.NativeRead.as_view()),
    ]
