from django.db import models


class Person(models.Model):
    # A symmetrical many-to-many relation to itself.
    name = models.CharField(max_length=50)
    friends = models.ManyToManyField("self", blank=True)

    def __str__(self):
        return self.name


class Shown(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(hidden=False)


class Label(models.Model):
    # A default manager that filters: Django's set() leaves hidden rows alone.
    name = models.CharField(max_length=50)
    hidden = models.BooleanField(default=False)

    objects = Shown()

    def __str__(self):
        return self.name


class Note(models.Model):
    text = models.CharField(max_length=50)
    labels = models.ManyToManyField(Label, blank=True)

    def __str__(self):
        return self.text


class Document(models.Model):
    # django-cleanup deletes replaced and deleted files; django-cacheops
    # caches its reads (tests/async_backend/test_on_commit.py).
    name = models.CharField(max_length=50)
    file = models.FileField(upload_to="documents/")

    def __str__(self):
        return self.name
