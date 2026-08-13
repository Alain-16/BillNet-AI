from django.db import models
from common.enums import (
    AccountingRefType,PostingStatus,TransactionPurpose, PurchaseStatus,SyncEntityType,SyncStatus
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




class QuickBooksPurchase(UUIDModel, CompanyOwnedModel, TimeStampedModel):

    connection = models.ForeignKey("accounts.IntegrationConnection",on_delete=models.PROTECT, related_name="purchases",null=True,blank=True)

    qbo_purchase_id = models.CharField(max_length=64)
    qbo_sync_token = models.CharField(max_length=32, blank=True)

    
    transaction_date = models.DateField(null=True, blank=True)
    total_amount = money_field(null=True, blank=True)
    currency = models.CharField(max_length=3, default="CAD")

    vendor_ref_id = models.CharField(max_length=64, blank=True)
    vendor_ref_name = models.CharField(max_length=255, blank=True)
    vendor_ref_type = models.CharField(max_length=20, blank=True)   # Vendor/Customer/Employee
    payment_account_ref_id = models.CharField(max_length=64, blank=True)
    payment_account_ref_name = models.CharField(max_length=255, blank=True)
    payment_type = models.CharField(max_length=20, blank=True)      # Cash/Check/CreditCard

    doc_number = models.CharField(max_length=64, blank=True)
    memo = models.CharField(max_length=512, blank=True)             # QBO PrivateNote
    total_tax = money_field(null=True, blank=True)

   
    is_credit = models.BooleanField(default=False)

    status = models.CharField(max_length=20, choices=PurchaseStatus.choices,
                              default=PurchaseStatus.ACTIVE)
    qbo_last_updated_at = models.DateTimeField(null=True, blank=True)
    local_last_synced_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)

  
    has_attachment = models.BooleanField(default=False)

    
    raw_payload = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
          
            models.UniqueConstraint(
                fields=["company", "qbo_purchase_id"],
                name="uniq_qbopurchase_company_extid",
            ),
        ]
        indexes = [
            models.Index(fields=["company", "transaction_date"]),
            models.Index(fields=["company", "total_amount"]),
            models.Index(fields=["company", "status"]),
            models.Index(fields=["company", "vendor_ref_id"]),
        ]

    def __str__(self) -> str:
        return f"QBO Purchase {self.qbo_purchase_id} ({self.total_amount} {self.currency})"


class QuickBooksPurchaseLine(UUIDModel,CompanyOwnedModel,TimeStampedModel):

    purchase = models.ForeignKey("accounting.QuickBooksPurchase",on_delete=models.CASCADE, related_name="lines")

    line_number = models.PositiveIntegerField(default=0)
    qbo_line_id = models.CharField(max_length=32, blank=True)
    detail_type = models.CharField(max_length=48, blank=True)

    description = models.CharField(max_length=512, blank=True)
    amount = money_field(null=True, blank=True)

    expense_account_ref_id = models.CharField(max_length=64, blank=True)
    expense_account_ref_name = models.CharField(max_length=255, blank=True)
    customer_ref_id = models.CharField(max_length=64, blank=True)     # = project
    customer_ref_name = models.CharField(max_length=255, blank=True)
    class_ref_id = models.CharField(max_length=64, blank=True)
    class_ref_name = models.CharField(max_length=255, blank=True)
    tax_code_ref_id = models.CharField(max_length=64, blank=True)
    item_ref_id = models.CharField(max_length=64, blank=True)
    item_ref_name = models.CharField(max_length=255, blank=True)

    raw = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["purchase", "line_number"]
        indexes = [models.Index(fields=["purchase","line_number"])]

    def __str__(self) -> str:
        return f"line {self.line_number}: {self.description[:40]} {self.amount}"


class QuickBooksSyncState(UUIDModel,CompanyOwnedModel,TimeStampedModel):


    connection = models.ForeignKey("accounts.IntegrationConnection", on_delete=models.PROTECT, related_name="sync_states", null=True,blank=True)

    entity_type = models.CharField(max_length=20, choices=SyncEntityType.choices)

    last_successful_cursor = models.DateTimeField(null=True, blank=True)
    last_webhook_received_at = models.DateTimeField(null=True, blank=True)
    last_cdc_started_at = models.DateTimeField(null=True, blank=True)
    last_cdc_completed_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=SyncStatus.choices,
                              default=SyncStatus.IDLE)
    last_error = models.JSONField(default=dict, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["company", "entity_type"],
                name="uniq_qbosyncstate_company_entity",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.entity_type} sync @ {self.last_successful_cursor}"


