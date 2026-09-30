from django.db import models


class Note(models.Model):
    """Lives in each tenant's schema."""

    text = models.CharField(max_length=100)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.text
