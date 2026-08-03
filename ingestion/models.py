from django.db import models
from common.enums import Provider
from common.models import CompanyOwnedModel,TimeStampedModel,UUIDModel


# Create your models here.


class SourceMessage(UUIDModel, TimeStampedModel,CompanyOwnedModel):


    connection = models.ForeignKey(
        "accounts.IntegrationConnection",
        on_delete=models.PROTECT,
        related_name="messages",
    )
    provider = models.CharField(max_length=20,choices=Provider.choices,default=Provider.GMAIL)
    provider_message_id = models.CharField(max_length=200)
    thread_id = models.CharField(max_length=200,blank=True)
    history_id = models.CharField(max_length=200,blank=True)
    sender = models.CharField(max_length=200,blank=True)
    subject = models.CharField(max_length=200,blank=True)
    received_at = models.DateTimeField(null=True,blank=True)
    source_link = models.CharField(max_length=1024,blank=True)
    raw_metadata = models.JSONField(default=dict,blank=True)

    class Meta:
        constraints =[
            models.UniqueConstraint(
                fields=["company","provider","external_id"],
                name="uniq_sourcemsg_company_provider_extid",
            ),
        ]
        indexes = [models.Index(fields=["company","provider"])]

    def __str__(self) -> str:
        return f"{self.sender}:{self.subject[:40]}"
    