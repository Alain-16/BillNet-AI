from django.db import models
import uuid


class UUIDModel(models.Model):

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True

class VersionedModel(models.Model):
    version = models.PositiveIntegerField(default=1)

    class Meta:
        abstract = True


class CompanyOwnedModel(models.Model):

    company = models.ForeignKey(
        "accounts.Company",
        on_delete=models.PROTECT,
        related_name="%(app_label)s_%(class)s_set",
        related_query_name="%(app_label)s_%(class)s",
    )

    class Meta:
        abstract = True

def money_field(**kwargs):

    return models.DecimalField(max_digits=10, decimal_places=2, **kwargs)


