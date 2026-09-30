from django.db import models
from django_mongodb_backend.fields import ArrayField, EmbeddedModelField, ObjectIdField
from django_mongodb_backend.models import EmbeddedModel


class Address(EmbeddedModel):
    street = models.CharField(max_length=50)
    city = models.CharField(max_length=50)


class Author(models.Model):
    name = models.CharField(max_length=50)

    def __str__(self):
        return self.name


class Tag(models.Model):
    name = models.CharField(max_length=50)

    def __str__(self):
        return self.name


class Book(models.Model):
    title = models.CharField(max_length=50)
    pages = models.IntegerField(default=0)
    author = models.ForeignKey(Author, models.CASCADE, related_name="books")
    tags = models.ManyToManyField(Tag, blank=True, related_name="books")
    address = EmbeddedModelField(Address, null=True, blank=True)
    keywords = ArrayField(models.CharField(max_length=20), default=list, blank=True)
    ref = ObjectIdField(null=True, blank=True)

    def __str__(self):
        return self.title


class Note(models.Model):
    """Lives on the standalone server, which has no transactions."""

    text = models.CharField(max_length=50)

    def __str__(self):
        return self.text


class StandaloneRouter:
    def db_for_read(self, model, **hints):
        return "standalone" if model is Note else None

    db_for_write = db_for_read

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label != "mongodb":
            return None
        return db == ("standalone" if model_name == "note" else "default")
