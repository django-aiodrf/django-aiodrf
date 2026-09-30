"""Model administration for local users, content and permission setup."""

from django.contrib import admin

from .models import Article, Note

admin.site.register(Article)
admin.site.register(Note)
