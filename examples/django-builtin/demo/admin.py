"""Expose the same model through Django's standard administration site."""

from django.contrib import admin

from .models import Entry

admin.site.register(Entry)
