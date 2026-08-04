from django.db import models
from common.enums import (
    ExpenseState,MappingScope,PaymentType,PolicyOutcome,ProjectStatus,VendorStatus
)
from common.models import (
    CompanyOwnedModel,TimeStampedModel,UUIDModel,VersionedModel,money_field,
)
# Create your models here.

class Vendor(UUIDModel, CompanyOwnedModel, TimeStampedModel, VersionedModel):
    
    name = models.CharField(max_length=255)                 # canonical normalized
    raw_names = models.JSONField(default=list, blank=True)  # original receipt spellings
    aliases = models.JSONField(default=list, blank=True)    # [{"type","value"}] folded VendorAlias
    sender_domains = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=20, choices=VendorStatus.choices,
                              default=VendorStatus.ACTIVE)
    qbo_vendor = models.ForeignKey(
        "accounting.AccountingReference", on_delete=models.PROTECT,
        null=True, blank=True, related_name="+",         # only VENDOR-type refs, enforced in service
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "name"],
                                    name="uniq_vendor_company_name"),
        ]

    def __str__(self) -> str:
        return self.name


class Project(UUIDModel, CompanyOwnedModel, TimeStampedModel, VersionedModel):
  
    code = models.CharField(max_length=64)
    name = models.CharField(max_length=255)
    aliases = models.JSONField(default=list, blank=True)
    status = models.CharField(max_length=20, choices=ProjectStatus.choices,
                              default=ProjectStatus.ACTIVE)
    active_from = models.DateField(null=True, blank=True)
    active_to = models.DateField(null=True, blank=True)
    qbo_customer = models.ForeignKey(
        "accounting.AccountingReference", on_delete=models.PROTECT,
        null=True, blank=True, related_name="+",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["company", "code"],
                                    name="uniq_project_company_code"),
        ]

    def __str__(self) -> str:
        return f"{self.code} · {self.name}"


class MappingRule(UUIDModel, CompanyOwnedModel, TimeStampedModel, VersionedModel):
   
    name = models.CharField(max_length=255)
    scope = models.CharField(max_length=20, choices=MappingScope.choices)
    conditions = models.JSONField(default=dict)   # {vendor_id, sender_domain, keywords, ...}
    outputs = models.JSONField(default=dict)       # {account_id, tax_code_id, project_id, ...}
    priority = models.IntegerField(default=100)    # lower = higher priority
    auto_post_permitted = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    effective_from = models.DateTimeField(null=True, blank=True)
    effective_to = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["priority"]
        indexes = [models.Index(fields=["company", "scope", "active"])]

    def __str__(self) -> str:
        return f"{self.scope} rule: {self.name}"


class Expense(UUIDModel, CompanyOwnedModel, TimeStampedModel, VersionedModel):

    source_document = models.ForeignKey(
        "documents.SourceDocument", on_delete=models.PROTECT, related_name="expenses",
    )
    interpretation = models.ForeignKey(   # the interpretation version currently in use
        "documents.DocumentInterpretation", on_delete=models.PROTECT,
        null=True, blank=True, related_name="expenses",
    )
    state = models.CharField(max_length=30, choices=ExpenseState.choices,
                             default=ExpenseState.DISCOVERED)

    # --- canonical money: real Decimals + explicit currency (Invariant #2) ---
    currency = models.CharField(max_length=3, default="CAD")
    subtotal = money_field(null=True, blank=True)
    tax_total = money_field(null=True, blank=True)
    total = money_field(null=True, blank=True)
    # per-code detail, e.g. [{"code":"GST","amount":"5.67","recoverable":true}]
    # amounts are STRINGS (Decimal-in-JSON discipline); the gate uses the Decimals above.
    tax_breakdown = models.JSONField(default=list, blank=True)

    transaction_date = models.DateField(null=True, blank=True)   # local business date
    vendor_raw_name = models.CharField(max_length=255, blank=True)
    receipt_number = models.CharField(max_length=128, blank=True)
    card_last_four = models.CharField(max_length=4, blank=True)  # never full PAN
    payment_type = models.CharField(max_length=20, choices=PaymentType.choices, blank=True)
    memo = models.CharField(max_length=512, blank=True)

    # --- current selected decisions (FKs to canonical entities) ---
    vendor = models.ForeignKey("expenses.Vendor", on_delete=models.PROTECT,
                               null=True, blank=True, related_name="expenses")
    project = models.ForeignKey("expenses.Project", on_delete=models.PROTECT,
                                null=True, blank=True, related_name="expenses")
    expense_account = models.ForeignKey("accounting.AccountingReference",
                                        on_delete=models.PROTECT, null=True, blank=True,
                                        related_name="+")
    tax_code_ref = models.ForeignKey("accounting.AccountingReference",
                                     on_delete=models.PROTECT, null=True, blank=True,
                                     related_name="+")
    payment_account = models.ForeignKey("accounting.AccountingReference",
                                        on_delete=models.PROTECT, null=True, blank=True,
                                        related_name="+")

    # --- folded review/policy JSON ---
    match_candidates = models.JSONField(default=dict, blank=True)      # per-dimension ranked
    validations = models.JSONField(default=list, blank=True)          # deterministic checks
    duplicate_candidates = models.JSONField(default=list, blank=True) # linkage signals
    policy_decision = models.JSONField(default=dict, blank=True)      # current snapshot
    policy_outcome = models.CharField(                                # promoted for querying/filters
        max_length=20, choices=PolicyOutcome.choices, blank=True,
    )

    class Meta:
        indexes = [
            models.Index(fields=["company", "state"]),
            models.Index(fields=["company", "policy_outcome"]),
            models.Index(fields=["company", "transaction_date"]),
        ]

    def __str__(self) -> str:
        return f"Expense {self.id} [{self.state}]"