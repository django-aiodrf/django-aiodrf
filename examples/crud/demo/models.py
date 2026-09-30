"""Database models used by the crud API."""

from django.db import models


class Tag(models.Model):
    name = models.CharField(max_length=40, unique=True)

    def __str__(self):
        return self.name


class Article(models.Model):
    title = models.CharField(max_length=120)
    tags = models.ManyToManyField(Tag, blank=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.title
