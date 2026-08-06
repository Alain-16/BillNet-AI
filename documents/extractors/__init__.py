from documents.extractors.base import (
    DocumentExtractor, ExtractedFieldDTO, ExtractionDTO, NullExtractor,
    EXTRACTION_SCHEMA_VERSION,
)
from documents.extractors.embedded_text import EmbeddedTextExtractor

_EXTRACTORS: list[DocumentExtractor] = [
    EmbeddedTextExtractor(),
    # slice 7:  TesseractExtractor(), TextractExtractor(), VisionExtractor(),
    NullExtractor(),          # must stay last
]


def get_extractor(*, mime_type: str, filename: str = "") -> DocumentExtractor:
    for extractor in _EXTRACTORS:
        if extractor.supports(mime_type=mime_type, filename=filename):
            return extractor
    return NullExtractor() 