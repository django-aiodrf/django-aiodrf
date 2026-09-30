import pydantic
import rules
from auditlog.registry import auditlog
from django.db import models
from django_cleanup import cleanup
from django_pydantic_field import SchemaField
from djmoney.models.fields import MoneyField
from phonenumber_field.modelfields import PhoneNumberField
from polymorphic.models import PolymorphicModel
from rules.contrib.models import RulesModel
from safedelete.models import SOFT_DELETE_CASCADE, SafeDeleteModel
from simple_history.models import HistoricalRecords
from taggit.managers import TaggableManager


class Document(models.Model):
    title = models.CharField(max_length=100)
    history = HistoricalRecords()

    def __str__(self):
        return self.title


# django-money, django-phonenumber-field, django-taggit, drf-extra-fields and
# django-pydantic-field (``test_money.py`` ... ``test_pydantic_field.py``).
class MoneyPrice(models.Model):
    name = models.CharField(max_length=100)
    price = MoneyField(max_digits=10, decimal_places=2, default_currency="EUR")

    def __str__(self):
        return self.name


class PhoneContact(models.Model):
    name = models.CharField(max_length=100)
    phone = PhoneNumberField()

    def __str__(self):
        return self.name


class TaggedArticle(models.Model):
    title = models.CharField(max_length=100)
    tags = TaggableManager(blank=True)

    def __str__(self):
        return self.title


class ExtraPhoto(models.Model):
    image = models.ImageField(upload_to="extra/")
    author = models.ForeignKey("testapp.Author", on_delete=models.CASCADE)

    def __str__(self):
        return self.image.name


class SchemaLimits(pydantic.BaseModel):
    rate: int = pydantic.Field(ge=0)
    burst: int = 1


class SchemaQuota(models.Model):
    name = models.CharField(max_length=100)
    limits = SchemaField(schema=SchemaLimits)

    def __str__(self):
        return self.name


# rules (``test_rules.py``): object permissions declared on the model.
@rules.predicate
def is_note_owner(user, note):
    return note is not None and note.owner_id == user.pk


class RulesNote(RulesModel):
    owner = models.ForeignKey("auth.User", on_delete=models.CASCADE)
    text = models.CharField(max_length=100)

    class Meta:
        rules_permissions = {
            "add": rules.is_authenticated,
            "view": is_note_owner,
            "change": is_note_owner,
            "delete": is_note_owner,
        }

    def __str__(self):
        return self.text


# django-polymorphic with django-rest-polymorphic (``test_polymorphic.py``).
class PolyProject(PolymorphicModel):
    topic = models.CharField(max_length=100)

    def __str__(self):
        return self.topic


class PolyArtProject(PolyProject):
    artist = models.CharField(max_length=100)


class PolyResearchProject(PolyProject):
    supervisor = models.CharField(max_length=100)


# django-safedelete (``test_safedelete.py``): delete() hides the row.
class SoftNote(SafeDeleteModel):
    _safedelete_policy = SOFT_DELETE_CASCADE
    title = models.CharField(max_length=100)

    def __str__(self):
        return self.title


class SoftComment(SafeDeleteModel):
    _safedelete_policy = SOFT_DELETE_CASCADE
    note = models.ForeignKey(
        SoftNote, on_delete=models.CASCADE, related_name="comments"
    )
    text = models.CharField(max_length=100)

    def __str__(self):
        return self.text


# django-auditlog (``test_auditlog.py``): who changed what, masked.
class AuditedNote(models.Model):
    title = models.CharField(max_length=100)
    secret = models.CharField(max_length=100, blank=True)

    def __str__(self):
        return self.title


auditlog.register(AuditedNote, mask_fields=["secret"])


# django-cleanup (``test_cleanup.py``), which handles only selected models.
@cleanup.select
class CleanedFile(models.Model):
    title = models.CharField(max_length=100)
    file = models.FileField(upload_to="cleanup/")

    def __str__(self):
        return self.title
