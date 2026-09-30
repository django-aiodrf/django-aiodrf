"""Database models used by the mongodb API."""

from django.db import models


class Note(models.Model):
    title = models.CharField(max_length=100)

    def __str__(self):
        return self.title
