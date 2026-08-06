from __future__ import annotations

import os
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from common.enums import (
    AuditActorType, AuditEventType, ClassificationMethod, DocumentType,
    ExtractionStatus, ScanStatus, SourceDocumentKind,
)
from common.errors import DomainError
from common.storage import build_object_key, get_object_storage, sha256_bytes
from documents.extractors import get_extractor
from documents.models import DocumentInterpretation, SourceDocument
from operations.services import record_event


ALLOWED_TYPES: dict[str, set[str]] = {
    "application/pdf": {".pdf"},
    "image/jpeg": {".jpg", ".jpeg"},
    "image/png": {".png"},
}
MAX_UPLOAD_BYTES = 15 * 1024 * 1024        # 15 MB


def sniff_mime(data: bytes) -> str:
 
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    return ""


def _validate_upload(*, data: bytes, filename: str) -> tuple[str, str]:
    
    if not data:
        raise DomainError("The uploaded file is empty.",
                          code="file_empty", field="file")
    if len(data) > MAX_UPLOAD_BYTES:
        raise DomainError(
            f"File is {len(data) // 1024 // 1024} MB; the limit is "
            f"{MAX_UPLOAD_BYTES // 1024 // 1024} MB.",
            code="file_too_large", field="file")

    mime = sniff_mime(data)
    if mime not in ALLOWED_TYPES:
        raise DomainError(
            "Only PDF, JPEG and PNG receipts are supported.",
            code="unsupported_media_type", field="file")

    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED_TYPES[mime]:
        
        raise DomainError(
            f"File extension '{ext or '(none)'}' does not match its actual "
            f"content type ({mime}).",
            code="extension_mismatch", field="file")
    return mime, ext


def _scan(document: SourceDocument) -> None:

    if not getattr(settings, "MALWARE_SCAN_ENABLED", False):
        document.scan_status = ScanStatus.CLEAN
        document.scanned_at = timezone.now()
        return
    raise NotImplementedError("Wire clamd here before enabling in production.")


@transaction.atomic
def ingest_upload(*, company, actor, data: bytes, filename: str) -> tuple[SourceDocument, bool]:

    mime, ext = _validate_upload(data=data, filename=filename)
    digest = sha256_bytes(data)


    existing = SourceDocument.objects.filter(company=company, sha256=digest,
                                             message__isnull=True).first()
    if existing:
        record_event(company=company, event_type=AuditEventType.FILE_CHECK,
                     aggregate_type="SourceDocument", aggregate_id=existing.id,
                     actor_type=AuditActorType.USER, actor_id=actor.id,
                     payload={"result": "duplicate_upload_ignored",
                              "sha256": digest, "filename": filename})
        return existing, False

    key = build_object_key(company.id, digest, ext)
    get_object_storage().put(key, data, content_type=mime)

    document = SourceDocument(
        company=company, message=None,
        kind=SourceDocumentKind.ATTACHMENT,
        provider_attachment_id="",
        object_key=key, sha256=digest, mime_type=mime,
        byte_size=len(data), filename=filename,
    )
    _scan(document)
    document.save()

    record_event(company=company, event_type=AuditEventType.DISCOVERY,
                 aggregate_type="SourceDocument", aggregate_id=document.id,
                 actor_type=AuditActorType.USER, actor_id=actor.id,
                 payload={"source": "manual_upload", "filename": filename,
                          "mime_type": mime, "byte_size": len(data),
                          "sha256": digest})
    record_event(company=company, event_type=AuditEventType.FILE_CHECK,
                 aggregate_type="SourceDocument", aggregate_id=document.id,
                 actor_type=AuditActorType.SYSTEM,
                 payload={"mime_ok": True, "extension_ok": True,
                          "size_ok": True, "scan_status": document.scan_status,
                          "scan_bypassed": not getattr(settings,
                                                       "MALWARE_SCAN_ENABLED", False)})
    return document, True


@transaction.atomic
def run_extraction(*, document: SourceDocument, actor=None) -> DocumentInterpretation:
    
    if document.scan_status != ScanStatus.CLEAN:
        raise DomainError(
            f"Document has not passed malware scanning (status: {document.scan_status}).",
            code="scan_not_clean")


    SourceDocument.objects.select_for_update().get(pk=document.pk)
    last = (DocumentInterpretation.objects.filter(document=document)
            .order_by("-version").first())
    next_version = (last.version + 1) if last else 1

    data = get_object_storage().get(document.object_key)
    extractor = get_extractor(mime_type=document.mime_type, filename=document.filename)
    result = extractor.extract(data, mime_type=document.mime_type,
                               filename=document.filename)

   
    if result.completed and result.text:
        from documents.parsers import parse_receipt_text
        result.fields = parse_receipt_text(result.text, method=result.method)

    interpretation = DocumentInterpretation.objects.create(
        company=document.company, document=document, version=next_version,
        # Classification is slice 7. An uploaded fixture is declared a receipt
        # by the human who uploaded it -- honest, and keeps AI off this path.
        document_type=DocumentType.RECEIPT,
        classification_method=ClassificationMethod.MANUAL,
        extraction_method=result.method,
        extraction_status=result.status,
        schema_version=result.schema_version,
        latency_ms=result.latency_ms,
        fields=result.fields_json(),
        evidence=result.evidence,
    )

    record_event(
        company=document.company, event_type=AuditEventType.EXTRACTION,
        aggregate_type="DocumentInterpretation", aggregate_id=interpretation.id,
        actor_type=AuditActorType.USER if actor else AuditActorType.SYSTEM,
        actor_id=getattr(actor, "id", ""), object_version=next_version,
        payload={"document_id": str(document.id), "status": result.status,
                 "method": result.method, "latency_ms": result.latency_ms,
                 "field_count": len(result.fields),
                 "reason": result.evidence.get("reason", "")},
    )
    return interpretation
