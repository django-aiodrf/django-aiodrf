"""Object permissions, model predicates and audit records for owned content."""

import rules
from auditlog.registry import auditlog
from django.conf import settings
from django.db import models
from rules.contrib.models import RulesModel
from simple_history.models import HistoricalRecords


class Article(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    title = models.CharField(max_length=120)
    sensitive_note = models.CharField(max_length=100, blank=True)
    history = HistoricalRecords(excluded_fields=["sensitive_note"])

    class Meta:
        ordering = ["pk"]

    def __str__(self):
        return self.title


auditlog.register(Article, mask_fields=["sensitive_note"])


@rules.predicate
def owns_note(user, note):
    return note is not None and note.owner_id == user.pk


class Note(RulesModel):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    text = models.CharField(max_length=120)

    class Meta:
        rules_permissions = {
            "add": rules.is_authenticated,
            "view": owns_note,
            "change": owns_note,
            "delete": owns_note,
        }

    def __str__(self):
        return self.text
