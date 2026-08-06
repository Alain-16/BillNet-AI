from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from common.enums import ExtractionStatus
from common.enums import ExtractionMethod

EXTRACTION_SCHEMA_VERSION = "1.0"


@dataclass
class ExtractedFieldDTO:

    name: str
    raw_value: str
    normalized_value: Any
    confidence: Decimal | None=None
    method: str=""
    source: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "raw_value": self.raw_value,
            "normalized_value": (str(self.normalized_value) if isinstance(self.self.normalized_value, Decimal) else self.normalized_value),
            "confidence": str(self.confidence) if self.confidence is not None else None,
            "method": self.method,
            "source": self.source,
        }


@dataclass
class ExtractionDTO:
    status: str
    method: str
    text: str=""
    fields: dict[str, ExtractedFieldDTO] = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    latency_ms: int | None = None
    schema_version: str = EXTRACTION_SCHEMA_VERSION

    @property
    def completed(self) -> bool:
        return self.status == ExtractionStatus.COMPLETED


    def fields_json(self) -> dict:
        return {name: f.to_json() for name, f in self.fields.items}


class DocumentExtractor(ABC):

    method: str

    @abstractmethod
    def supports(self, *, mime_type: str, filename: str) -> bool:
        """Can this adapter even attempt this document? Cheap check, no I/O."""

    @abstractmethod
    def extract(self, data: bytes, *, mime_type: str, filename: str) -> ExtractionDTO:
        """Never raises for a readable-but-unparseable document -- return
        ABSTAINED. Reserve FAILED for genuinely broken input."""


class NullExtractor(DocumentExtractor):

    method = ""

    def supports(self, *, mime_type: str, filename: str) -> bool:
        return True

    def extract(self, data: bytes, *, mime_type: str, filename: str) -> ExtractionDTO:
        return ExtractionDTO(
            status=ExtractionStatus.ABSTAINED,
            method=ExtractionMethod.EMBEDDED_TEXT,
            evidence={"reason": "unsupported_media_type",
                      "detail": f"No extractor for {mime_type or 'unknown type'}. "
                                f"Enter the values manually, or wait for OCR.",
                      "retry_when": "ocr_adapter_available"},
        )