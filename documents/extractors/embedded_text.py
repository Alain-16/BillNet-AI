import io
import time

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from common.enums import ExtractionMethod, ExtractionStatus
from documents.extractors.base import DocumentExtractor, ExtractionDTO

MIN_TEXT_CHARS = 40


class EmbeddedTextExtractor(DocumentExtractor):
    method = ExtractionMethod.EMBEDDED_TEXT

    def supports(self, *, mime_type: str, filename: str) -> bool:
        return mime_type == "application/pdf"

    def extract(self, data: bytes, *, mime_type: str, filename: str) -> ExtractionDTO:
        started = time.monotonic()

        def elapsed() -> int:
            return int((time.monotonic() - started) * 1000)

        try:
            reader = PdfReader(io.BytesIO(data))
        except (PdfReadError, Exception) as exc:      # noqa: B014 - pypdf is broad
            # FAILED, not ABSTAINED: nobody can read this, not even a human.
            # Routes to an ExceptionCase rather than to manual review.
            return ExtractionDTO(
                status=ExtractionStatus.FAILED, method=self.method,
                latency_ms=elapsed(),
                evidence={"reason": "corrupt_file", "detail": str(exc)[:300]},
            )

        if reader.is_encrypted:
            # Try the empty password -- many receipts are "encrypted" with none.
            try:
                if reader.decrypt("") == 0:
                    raise PdfReadError("password required")
            except Exception:
                return ExtractionDTO(
                    status=ExtractionStatus.FAILED, method=self.method,
                    latency_ms=elapsed(),
                    evidence={"reason": "password_protected",
                              "detail": "The PDF requires a password."},
                )

        pages: list[str] = []
        for page in reader.pages:
            try:
                pages.append(page.extract_text() or "")
            except Exception:
                pages.append("")          # one bad page must not kill the document

        text = "\n".join(pages)

        if len(text.strip()) < MIN_TEXT_CHARS:
            # THE ABSTENTION PATH -- the single most important branch in this
            # slice. Scanned or photographed PDF: no text layer to read.
            return ExtractionDTO(
                status=ExtractionStatus.ABSTAINED, method=self.method,
                text=text, latency_ms=elapsed(),
                evidence={"reason": "no_text_layer",
                          "detail": f"Only {len(text.strip())} characters of embedded "
                                    f"text across {len(reader.pages)} page(s); this "
                                    f"looks like a scan or photo.",
                          "retry_when": "ocr_adapter_available"},
            )

        return ExtractionDTO(
            status=ExtractionStatus.COMPLETED, method=self.method,
            text=text, latency_ms=elapsed(),
            evidence={"pages": len(reader.pages), "chars": len(text)},
        )
