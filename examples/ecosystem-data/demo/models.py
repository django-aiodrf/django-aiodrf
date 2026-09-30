"""Models used to exercise vendor fields, nested writes and deletion policies."""

from django.db import models
from django_cleanup import cleanup
from django_pydantic_field import SchemaField
from djmoney.models.fields import MoneyField
from phonenumber_field.modelfields import PhoneNumberField
from polymorphic.models import PolymorphicModel
from pydantic import BaseModel, Field
from safedelete.models import SOFT_DELETE, SafeDeleteModel
from taggit.managers import TaggableManager


class Limits(BaseModel):
    rate: int = Field(ge=0)
    burst: int = Field(default=1, ge=1)


def default_limits():
    return {"rate": 0, "burst": 1}


class Author(models.Model):
    name = models.CharField(max_length=100)
    phone = PhoneNumberField(blank=True)

    def __str__(self):
        return self.name


class Book(models.Model):
    title = models.CharField(max_length=150)
    author = models.ForeignKey(Author, related_name="books", on_delete=models.CASCADE)
    price = MoneyField(
        max_digits=10,
        decimal_places=2,
        default_currency="EUR",
        default=0,
        currency_choices=[
            ("EUR", "Euro"),
            ("USD", "US Dollar"),
            ("GBP", "Pound Sterling"),
        ],
    )
    tags = TaggableManager(blank=True)
    limits = SchemaField(schema=Limits, default=default_limits)

    def __str__(self):
        return self.title


@cleanup.select
class Photo(models.Model):
    image = models.ImageField(upload_to="photos/")
    author = models.ForeignKey(Author, on_delete=models.CASCADE)

    def __str__(self):
        return self.image.name


class ArchivedNote(SafeDeleteModel):
    _safedelete_policy = SOFT_DELETE
    text = models.CharField(max_length=150)

    def __str__(self):
        return self.text


class Project(PolymorphicModel):
    topic = models.CharField(max_length=150)

    def __str__(self):
        return self.topic


class ArtProject(Project):
    artist = models.CharField(max_length=100)
