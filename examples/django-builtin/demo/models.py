"""Database defaults and computed output represented by ordinary DRF fields."""

from django.db import models


class Entry(models.Model):
    title = models.CharField(max_length=120)
    amount = models.IntegerField(db_default=1)
    total = models.GeneratedField(
        expression=models.F("amount") * 2,
        output_field=models.IntegerField(),
        db_persist=True,
    )

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        return self.title
