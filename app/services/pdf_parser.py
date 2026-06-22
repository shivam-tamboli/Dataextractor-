"""
PDF text and table extraction.
Tries native text extraction first; falls back to OCR for scanned pages.
"""
import io
import logging
from dataclasses import dataclass, field
from typing import List, Optional

import pdfplumber
import pytesseract
from PIL import Image

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


@dataclass
class PageBlock:
    page: int
    text: str
    via_ocr: bool = False


@dataclass
class TableBlock:
    page: int
    rows: List[List[Optional[str]]]


@dataclass
class ParsedDocument:
    page_count: int
    text_blocks: List[PageBlock] = field(default_factory=list)
    tables: List[TableBlock] = field(default_factory=list)
    is_scanned: bool = False


class PDFParser:
    """Extract text and tables from a PDF file (bytes)."""

    # Minimum non-whitespace characters on a page to skip OCR
    _MIN_TEXT_LEN = 20

    def parse(self, file_bytes: bytes) -> ParsedDocument:
        result = ParsedDocument(page_count=0)

        try:
            with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
                result.page_count = len(pdf.pages)

                for page_num, page in enumerate(pdf.pages, start=1):
                    self._process_page(page, page_num, result)

        except Exception as exc:
            logger.exception("Failed to parse PDF: %s", exc)
            raise RuntimeError(f"PDF parsing failed: {exc}") from exc

        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _process_page(self, page, page_num: int, result: ParsedDocument) -> None:
        native_text = page.extract_text() or ""

        if len(native_text.strip()) >= self._MIN_TEXT_LEN:
            result.text_blocks.append(
                PageBlock(page=page_num, text=native_text, via_ocr=False)
            )
        else:
            ocr_text = self._ocr_page(page)
            if ocr_text:
                result.is_scanned = True
                result.text_blocks.append(
                    PageBlock(page=page_num, text=ocr_text, via_ocr=True)
                )

        self._extract_tables(page, page_num, result)

    def _ocr_page(self, page) -> str:
        try:
            img: Image.Image = page.to_image(resolution=300).original
            text: str = pytesseract.image_to_string(img, config="--psm 6")
            return text.strip()
        except Exception as exc:
            logger.warning("OCR failed on page: %s", exc)
            return ""

    def _extract_tables(self, page, page_num: int, result: ParsedDocument) -> None:
        try:
            raw_tables = page.extract_tables() or []
            for raw in raw_tables:
                if not raw:
                    continue
                # Normalise cells to str | None
                rows = [
                    [cell.strip() if isinstance(cell, str) else cell for cell in row]
                    for row in raw
                ]
                result.tables.append(TableBlock(page=page_num, rows=rows))
        except Exception as exc:
            logger.warning("Table extraction failed on page %d: %s", page_num, exc)
