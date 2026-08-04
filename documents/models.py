from django.db import models
from common.enums import (
    ClassificationMethod,DocumentType,ExtractionMethod,ExtractionStatus,ScanStatus,SourceDocumentKind
)
from common.models import CompanyOwnedModel,TimeStampedModel,UUIDModel

# Create your models here.



class SourceDocument(UUIDModel, CompanyOwnedModel, TimeStampedModel):

    message = models.ForeignKey(
        "ingestion.SourceMessage", on_delete=models.PROTECT,
        related_name="documents", null=True, blank=True,   # null allows future non-email sources
    )
    kind = models.CharField(max_length=20, choices=SourceDocumentKind.choices,
                            default=SourceDocumentKind.ATTACHMENT)
    provider_attachment_id = models.CharField(max_length=255, blank=True)  # "" for email body

    object_key = models.CharField(max_length=512)   # S3 key of stored bytes
    sha256 = models.CharField(max_length=64)        # dedup + immutability anchor
    mime_type = models.CharField(max_length=100, blank=True)
    byte_size = models.BigIntegerField(default=0)
    filename = models.CharField(max_length=255, blank=True)

    scan_status = models.CharField(max_length=20, choices=ScanStatus.choices,
                                   default=ScanStatus.PENDING)
    scanned_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            # Idempotent ingestion: same attachment on same message = one row.
            models.UniqueConstraint(
                fields=["message", "provider_attachment_id"],
                name="uniq_document_message_attachment",
            ),
        ]
        # sha256 indexed (NOT unique): the same bytes may legitimately arrive via
        # a forwarded email; the duplicate layer decides, not a hard DB constraint.
        indexes = [models.Index(fields=["company", "sha256"])]

    def __str__(self) -> str:
        return self.filename or f"{self.kind}:{self.sha256[:12]}"


class DocumentInterpretation(UUIDModel, CompanyOwnedModel, TimeStampedModel):

    document = models.ForeignKey(
        "documents.SourceDocument", on_delete=models.PROTECT,
        related_name="interpretations",
    )
    version = models.PositiveIntegerField(default=1)   # append-only sequence, not optimistic

    # classification (folds DocumentClassification)
    document_type = models.CharField(max_length=20, choices=DocumentType.choices,
                                     default=DocumentType.UNKNOWN)
    classification_method = models.CharField(max_length=10, choices=ClassificationMethod.choices)
    classification_confidence = models.DecimalField(max_digits=5, decimal_places=4,
                                                    null=True, blank=True)

    # extraction (folds ExtractionResult)
    extraction_method = models.CharField(max_length=20, choices=ExtractionMethod.choices,
                                         blank=True)
    extraction_status = models.CharField(max_length=20, choices=ExtractionStatus.choices,
                                         default=ExtractionStatus.PENDING)
    schema_version = models.CharField(max_length=32, blank=True)
    prompt_version = models.CharField(max_length=32, blank=True)
    model_identifier = models.CharField(max_length=64, blank=True)   # e.g. gpt-...
    latency_ms = models.IntegerField(null=True, blank=True)

    fields = models.JSONField(default=dict, blank=True)              # folds ExtractedField
    evidence = models.JSONField(default=dict, blank=True)            # classification evidence
    raw_response_ref = models.CharField(max_length=512, blank=True)  # object key, not inline

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["document", "version"],
                name="uniq_interpretation_document_version",
            ),
        ]
        ordering = ["document", "-version"]

    def __str__(self) -> str:
        return f"{self.document_type} v{self.version}"