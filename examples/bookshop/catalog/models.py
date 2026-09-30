"""Database models used by the bookshop API."""

from django.db import models


class Author(models.Model):
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name


class Book(models.Model):
    sku = models.CharField(max_length=20, unique=True)
    title = models.CharField(max_length=200)
    author = models.ForeignKey(Author, related_name="books", on_delete=models.PROTECT)
    updated = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title
