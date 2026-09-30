"""A durable work request whose publication follows its database commit."""

from django.db import models


class Job(models.Model):
    title = models.CharField(max_length=100)
    completed = models.BooleanField(default=False)

    def __str__(self):
        return self.title
