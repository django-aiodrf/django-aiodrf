from django.conf import settings
from django.db import models


class Author(models.Model):
    name = models.CharField(max_length=100)

    def __str__(self):
        return self.name


class Tag(models.Model):
    name = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.name


class Book(models.Model):
    title = models.CharField(max_length=200)
    isbn = models.CharField(max_length=13, unique=True)
    pages = models.PositiveIntegerField(default=100)
    author = models.ForeignKey(Author, related_name="books", on_delete=models.CASCADE)
    tags = models.ManyToManyField(Tag, related_name="books", blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL
    )

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.title

    async def asummary(self):
        return f"{self.title} ({self.pages} pages)"


class Edition(models.Model):
    """Covers the field types the serializer compiler handles."""

    code = models.UUIDField()
    book = models.ForeignKey(Book, related_name="editions", on_delete=models.CASCADE)
    translator = models.ForeignKey(
        Author, null=True, blank=True, on_delete=models.SET_NULL
    )
    published = models.DateTimeField()
    released = models.DateField(null=True)
    active = models.BooleanField(default=True)
    rating = models.FloatField(null=True)
    price = models.DecimalField(max_digits=6, decimal_places=2)
    format = models.CharField(
        max_length=10, choices=[("hb", "Hardback"), ("pb", "Paperback")]
    )
    extra = models.JSONField(default=dict)
    notes = models.TextField(blank=True)

    def __str__(self):
        return f"{self.book} ({self.format})"


class Attachment(models.Model):
    """Uploaded files: multipart parsing and storage writes happen off the loop."""

    title = models.CharField(max_length=100)
    file = models.FileField(upload_to="attachments/")

    def __str__(self):
        return self.title


class Shipment(models.Model):
    """A composite primary key (Django 5.2)."""

    pk = models.CompositePrimaryKey("carrier", "number")
    carrier = models.CharField(max_length=20)
    number = models.IntegerField()
    note = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return f"{self.carrier} {self.number}"


class Invoice(models.Model):
    """Values the database computes: ``db_default`` and a ``GeneratedField``."""

    net = models.IntegerField()
    tax = models.IntegerField(db_default=5)
    total = models.GeneratedField(
        expression=models.F("net") + models.F("tax"),
        output_field=models.IntegerField(),
        db_persist=True,
    )

    def __str__(self):
        return f"invoice {self.pk}"


class Seat(models.Model):
    """A unique constraint with a condition: one open booking per seat number."""

    number = models.IntegerField()
    open = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["number"], condition=models.Q(open=True), name="one_open_seat"
            )
        ]

    def __str__(self):
        return f"seat {self.number}"
