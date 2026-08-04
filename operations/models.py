from django.db import models
from common.enums import (
    AuditActorType,AuditEventType,ExceptionStatus,ExceptionSeverity
)
from common.models import CompanyOwnedModel,TimeStampedModel,UUIDModel

# Create your models here.

class AuditEvent(UUIDModel, CompanyOwnedModel, TimeStampedModel):

    event_type = models.CharField(max_length=30, choices=AuditEventType.choices)
    actor_type = models.CharField(max_length=20, choices=AuditActorType.choices,
                                  default=AuditActorType.SYSTEM)
    actor_id = models.CharField(max_length=64, blank=True)     # user id / worker / model
    correlation_id = models.CharField(max_length=64, blank=True)

    aggregate_type = models.CharField(max_length=64)           # "Expense", "SourceDocument", ...
    aggregate_id = models.CharField(max_length=64)             # UUID as string (generic)
    object_version = models.PositiveIntegerField(null=True, blank=True)

    payload = models.JSONField(default=dict, blank=True)       # redacted; folds AI + review detail

    class Meta:
        indexes = [
            models.Index(fields=["company", "aggregate_type", "aggregate_id"]),
            models.Index(fields=["company", "event_type"]),
            models.Index(fields=["correlation_id"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.event_type} @ {self.aggregate_type}:{self.aggregate_id}"


class ExceptionCase(UUIDModel, CompanyOwnedModel, TimeStampedModel):

    code = models.CharField(max_length=64)                     # machine-readable
    stage = models.CharField(max_length=40, blank=True)        # pipeline stage
    severity = models.CharField(max_length=10, choices=ExceptionSeverity.choices,
                                default=ExceptionSeverity.MEDIUM)
    retryable = models.BooleanField(default=False)
    status = models.CharField(max_length=20, choices=ExceptionStatus.choices,
                              default=ExceptionStatus.OPEN)
    message = models.TextField(blank=True)                     # user-readable explanation

    aggregate_type = models.CharField(max_length=64, blank=True)
    aggregate_id = models.CharField(max_length=64, blank=True)
    expense = models.ForeignKey("expenses.Expense", on_delete=models.PROTECT,
                                null=True, blank=True, related_name="exceptions")
    correlation_id = models.CharField(max_length=64, blank=True)

    owner = models.ForeignKey("accounts.User", on_delete=models.PROTECT,
                              null=True, blank=True, related_name="assigned_exceptions")
    resolution = models.TextField(blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    context = models.JSONField(default=dict, blank=True)       # redacted diagnostics

    class Meta:
        indexes = [
            models.Index(fields=["company", "status", "severity"]),
            models.Index(fields=["company", "code"]),
        ]

    def __str__(self) -> str:
        return f"{self.code} [{self.status}]"