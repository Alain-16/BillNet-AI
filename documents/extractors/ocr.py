from __future__ import annotations
import io
import time

from common.enums import ExtractionMethod, ExtractionStatus
from documents.extractors.base import DocumentExtractor, ExtractionDTO


MIN_TEXT_CHARS = 50
MIN_MEAN_CONFIDENCE = 45.0
TESSERACT_CONFIG = "--psm 4"

class TesseractExtractor(DocumentExtractor):
    """The free OCR baseline named in workflow doc 4, item 4.

    Deliberately NOT a preprocessing pipeline. Grayscale + autocontrast +
    binarize was measured against the raw image and performed WORSE: "Total"
    became "Tota]" and the vendor line was lost. Faded thermal paper may yet
    need it -- add it then, behind a measurement, not now on principle.
    """

    method = ExtractionMethod.OCR

    def supports(self, *, mime_type: str, filename: str) -> bool:
        # PDFs included: this is the FALLBACK rung for an image-only PDF whose
        # text layer the embedded-text extractor already declined.
        return mime_type in ("application/pdf", "image/jpeg", "image/png")

    def extract(self, data: bytes, *, mime_type: str, filename: str) -> ExtractionDTO:
        started = time.monotonic()

        def elapsed() -> int:
            return int((time.monotonic() - started) * 1000)

        try:
            from PIL import Image
            import pytesseract
            from pytesseract import Output
        except ImportError as exc:
            # A missing dependency is an OPERATIONAL fault, not a bad document.
            # Abstain so the receipt still reaches a human for manual entry --
            # never FAILED, which would route a perfectly good receipt to an
            # exception queue because of a deployment mistake.
            return ExtractionDTO(
                status=ExtractionStatus.ABSTAINED, method=self.method,
                latency_ms=elapsed(),
                evidence={"reason": "ocr_unavailable", "detail": str(exc)[:200],
                          "retry_when": "ocr_dependencies_installed"},
            )

        image, source_note = self._load_image(data, mime_type=mime_type)
        if image is None:
            return ExtractionDTO(
                status=ExtractionStatus.ABSTAINED, method=self.method,
                latency_ms=elapsed(),
                evidence={"reason": "no_image_found", "detail": source_note,
                          "retry_when": "page_renderer_available"},
            )

        try:
            text = pytesseract.image_to_string(image, config=TESSERACT_CONFIG)
            data_dict = pytesseract.image_to_data(
                image, config=TESSERACT_CONFIG, output_type=Output.DICT)
        except Exception as exc:
            return ExtractionDTO(
                status=ExtractionStatus.ABSTAINED, method=self.method,
                latency_ms=elapsed(),
                evidence={"reason": "ocr_error", "detail": str(exc)[:200]},
            )

        confidences = [int(c) for c in data_dict.get("conf", []) if int(c) >= 0]
        mean_conf = (sum(confidences) / len(confidences)) if confidences else 0.0
        chars = len(text.strip())

        # THE ABSTENTION CONTRACT, unchanged from EmbeddedTextExtractor.
        # An adapter that returns 12 characters of noise as COMPLETED is worse
        # than one that abstains: every validator downstream then has to defend
        # against phantom data forever.
        if chars < MIN_TEXT_CHARS or mean_conf < MIN_MEAN_CONFIDENCE:
            return ExtractionDTO(
                status=ExtractionStatus.ABSTAINED, method=self.method,
                text=text, latency_ms=elapsed(),
                evidence={"reason": "ocr_low_quality",
                          "detail": f"OCR read {chars} characters at mean "
                                    f"confidence {mean_conf:.0f}; too unreliable "
                                    f"to use. Enter the values manually.",
                          "chars": chars, "mean_confidence": round(mean_conf, 1),
                          "source": source_note},
            )

        return ExtractionDTO(
            status=ExtractionStatus.COMPLETED, method=self.method,
            text=text, latency_ms=elapsed(),
            evidence={"chars": chars, "words": len(confidences),
                      "mean_confidence": round(mean_conf, 1),
                      "psm": TESSERACT_CONFIG, "source": source_note},
        )

    # ---------------------------------------------------------------- helpers

    def _load_image(self, data: bytes, *, mime_type: str):
        """Returns (PIL.Image | None, note). Two input shapes, one output."""
        from PIL import Image

        if mime_type in ("image/jpeg", "image/png"):
            try:
                return Image.open(io.BytesIO(data)), "uploaded_image"
            except Exception as exc:
                return None, f"unreadable_image: {str(exc)[:120]}"

        return self._largest_pdf_image(data)

    def _largest_pdf_image(self, data: bytes):
        """Pull the page image out of an image-only PDF.

        LARGEST BY PIXEL AREA, not first. The WINNERS scan carries two images
        on page 1 -- the receipt at 1580x2844 and a 240x90 logo. Taking
        page.images[0] blindly is a coin flip on whether you OCR the logo.

        Using pypdf's embedded images avoids a poppler / pdf2image system
        dependency. The limit is real: it only works when the scanner embedded
        whole images. A page drawn as vector art or image tiles yields nothing
        useful, and that case needs a true page renderer -- until then it
        abstains, which is the honest answer.
        """
        from PIL import Image
        from pypdf import PdfReader

        try:
            reader = PdfReader(io.BytesIO(data))
        except Exception as exc:
            return None, f"unreadable_pdf: {str(exc)[:120]}"

        best, best_area = None, 0
        for page in reader.pages:
            try:
                embedded = list(page.images)
            except Exception:
                continue
            for item in embedded:
                try:
                    image = Image.open(io.BytesIO(item.data))
                except Exception:
                    continue
                area = image.size[0] * image.size[1]
                if area > best_area:
                    best, best_area = image, area

        if best is None:
            return None, "pdf_has_no_embedded_images"
        return best, f"pdf_embedded_image_{best.size[0]}x{best.size[1]}"