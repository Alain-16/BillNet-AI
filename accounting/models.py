from django.db import models
from common.enums import (
    AccountingRefType,PostingStatus,TransactionPurpose
    )
from common.models import CompanyOwnedModel,TimeStampedModel,UUIDModel, money_field


# Create your models here.

class AccountingReference(UUIDModel, TimeStampedModel,CompanyOwnedModel):

    connection = models.ForeignKey(
        "accounts.IntegrationConnection",
        on_delete=models.PROTECT,
        related_name="references",null=True,blank=True
    )

    entity_type= models.CharField(max_length=30,choices=AccountingRefType.choices)
    external_id = models.CharField(max_length=255)
    name = models.CharField(max_length=255)
    active = models.BooleanField(default=True)
    sync_token = models.CharField(max_length=255,blank=True)
    qbo_updated_at = models.DateTimeField(null=True,blank=True)
    data = models.JSONField(default=dict,blank=True)

    class Meta:
        constraints =[
            models.UniqueConstraint(
                fields=["company","entity_type","external_id"],
                name="uniq_accref_company_type_extid",
            ),
        ]
        indexes = [models.Index(fields=["company","entity_type","active"])]

    def __str__(self) -> str:
        return f"{self.entity_type}:{self.name}"
    

class PostingIntent(UUIDModel, CompanyOwnedModel, TimeStampedModel):

    expense = models.ForeignKey(
        "expenses.Expense", on_delete=models.PROTECT, related_name="posting_intents",
    )
    expense_version = models.PositiveIntegerField()    # the version this intent targets
    transaction_purpose = models.CharField(
        max_length=20, choices=TransactionPurpose.choices,
        default=TransactionPurpose.PURCHASE,
    )

    idempotency_key = models.CharField(max_length=128, unique=True)  # (expense, version, purpose)
    request_id = models.UUIDField()                    # QBO RequestId, stable per intent
    doc_number_token = models.CharField(max_length=32) # short collision-resistant token
    minor_version = models.CharField(max_length=16, blank=True)      # pinned QBO minorversion

    status = models.CharField(
        max_length=20, choices=PostingStatus.choices, default=PostingStatus.PENDING,
    )
    attempt_count = models.PositiveIntegerField(default=0)
    claimed_at = models.DateTimeField(null=True, blank=True)
    claimed_by = models.CharField(max_length=64, blank=True)         # worker id

    # folded AccountingTransaction (populated on confirmed success)
    qbo_entity_type = models.CharField(max_length=32, blank=True)
    qbo_entity_id = models.CharField(max_length=64, blank=True)
    qbo_sync_token = models.CharField(max_length=32, blank=True)
    posted_at = models.DateTimeField(null=True, blank=True)
    response_summary = models.JSONField(default=dict, blank=True)    # safe metadata only
    last_error = models.JSONField(default=dict, blank=True)          # {code, retryable, ...}

    class Meta:
        constraints = [
            # One posting intent per expense version + purpose (Invariant #4).
            models.UniqueConstraint(
                fields=["expense", "expense_version", "transaction_purpose"],
                name="uniq_intent_expense_version_purpose",
            ),
            # A confirmed QBO entity can't be claimed by two intents in a company.
            models.UniqueConstraint(
                fields=["company", "qbo_entity_id"],
                condition=models.Q(qbo_entity_id__gt=""),
                name="uniq_intent_company_qbo_entity",
            ),
        ]

    def __str__(self) -> str:
        return f"PostingIntent {self.idempotency_key} [{self.status}]"




