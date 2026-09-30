"""A small resource for profiling and middleware integration tests."""

from django.db import models


class Record(models.Model):
    title = models.CharField(max_length=100)

    def __str__(self):
        return self.title
