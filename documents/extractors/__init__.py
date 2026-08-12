from documents.extractors.base import (
    DocumentExtractor, ExtractedFieldDTO, ExtractionDTO, NullExtractor,
    EXTRACTION_SCHEMA_VERSION,
)
from documents.extractors.embedded_text import EmbeddedTextExtractor
from documents.extractors.ocr import TesseractExtractor

_EXTRACTORS: list[DocumentExtractor] = [
    EmbeddedTextExtractor(),
    TesseractExtractor(),
    # slice 7:  TesseractExtractor(), TextractExtractor(), VisionExtractor(),
    NullExtractor(),          # must stay last
]

def get_extraction_chain(*, mime_type:str, filename: str = "") -> list[DocumentExtractor]:

    chain = [e for e in _EXTRACTORS
             if e.supports(mime_type=mime_type,filename=filename)]
    return chain or [NullExtractor]


def get_extractor(*, mime_type: str, filename: str = "") -> DocumentExtractor:
    
    return get_extraction_chain(mime_type=mime_type,filename=filename)[0]