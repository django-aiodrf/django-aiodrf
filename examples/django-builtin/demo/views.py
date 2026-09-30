"""Django services called through explicit async API boundaries."""

from django import forms
from django.contrib import messages
from django.core.cache import cache
from django.core.mail import send_mail
from django.db import transaction
from django.dispatch import Signal, receiver
from django.template.response import TemplateResponse
from django.utils import timezone, translation
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated

from aiodrf.response import Response
from aiodrf.utils import run_sync
from aiodrf.views import APIView
from aiodrf.viewsets import ModelViewSet

from .models import Entry


class EntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = Entry
        fields = ["id", "title", "amount", "total"]
        read_only_fields = ["total"]
        extra_kwargs = {"amount": {"required": False}}


class Entries(ModelViewSet):
    queryset = Entry.objects.all()
    serializer_class = EntrySerializer

    def perform_create(self, serializer):
        # Explicit ownership includes the callback and all custom writes.
        with transaction.atomic():
            entry = serializer.save()
            transaction.on_commit(lambda: cache.set("last-entry", entry.pk, timeout=30))


class Value(serializers.Serializer):
    value = serializers.CharField(max_length=100)


class CachedValue(APIView):
    async def get(self, request):
        return Response({"value": await cache.aget("sample")})

    async def post(self, request):
        from aiodrf import aio

        serializer = Value(data=await request.adata())
        await aio.is_valid(serializer, raise_exception=True)
        await cache.aset("sample", serializer.validated_data["value"], timeout=30)
        return Response({"stored": True})


class ContactForm(forms.Form):
    email = forms.EmailField()
    message = forms.CharField(max_length=500)


class Contact(APIView):
    def post(self, request):
        # Form validation and synchronous mail delivery run in one worker.
        form = ContactForm(request.data)
        if not form.is_valid():
            return Response(form.errors, status=400)
        send_mail(
            "Example contact",
            form.cleaned_data["message"],
            "example@localhost",
            [form.cleaned_data["email"]],
        )
        return Response({"accepted": True}, status=202)


notification = Signal()


@receiver(notification)
async def notification_received(sender, value, **kwargs):
    return value.upper()


class Notifications(APIView):
    async def post(self, request):
        from aiodrf import aio

        serializer = Value(data=await request.adata())
        await aio.is_valid(serializer, raise_exception=True)
        responses = await notification.asend(
            sender=type(self), **serializer.validated_data
        )
        return Response({"results": [result for _, result in responses]})


class SessionDetails(APIView):
    permission_classes = [IsAuthenticated]

    async def get(self, request):
        queued = await run_sync(list)(messages.get_messages(request._request))
        return Response(
            {
                "user": request.user.get_username(),
                "language": translation.get_language(),
                "timezone": timezone.get_current_timezone_name(),
                "messages": [str(item) for item in queued],
            }
        )


class Template(APIView):
    def get(self, request):
        return TemplateResponse(
            request._request, "demo/index.html", {"count": Entry.objects.count()}
        )
