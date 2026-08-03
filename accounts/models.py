from django.db import models
from common.enums import CompanyStatus
from common.models import UUIDModel, TimeStampedModel

# Create your models here.

class Company(UUIDModel, TimeStampedModel):

    name = models.CharField(max_length=255)

    currency = models.CharField(max_length=3, default="CAD")

    status = models.CharField(
        max_length=30,
        choices=CompanyStatus.choices,
        default=CompanyStatus.ACTIVE,
    )

    auto_post_enabled = models.BooleanField(default=False)

    tax_codes = models.JSONField(default=list)

    class Meta:
        verbose_name_plural = "companies"

    def __str__(self)->str:
        return self.name