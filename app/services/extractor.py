"""
Key-value extraction from parsed PDF content.

Three main strategies:
  1. Colon-separated  — "Invoice No: INV-001"
  2. Multi-line       — label on one line, value on the next
  3. Table rows       — 2-column key/value tables; N-column header+data tables
  4. Regex anchors    — dates, amounts, GSTIN, PAN, phone, email, etc.
"""
import logging
import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from app.services.pdf_parser import ParsedDocument, PageBlock, TableBlock

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Domain-specific regex catalogue
# ---------------------------------------------------------------------------
_DATE = re.compile(
    r"\b(\d{1,2}[-/]\w{3,9}[-/]\d{2,4}"
    r"|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}"
    r"|\w{3,9}\s+\d{1,2},?\s+\d{4})\b",
    re.IGNORECASE,
)
_AMOUNT = re.compile(
    r"(?:Rs\.?|INR|USD|\$|€|£)\s*(\d{1,3}(?:,\d{3})*(?:\.\d{1,2})?)"
    r"|(\d{1,3}(?:,\d{3})+(?:\.\d{1,2})?)",
)
_GSTIN = re.compile(
    r"\b\d{2}[A-Z]{5}\d{4}[A-Z][A-Z\d]Z[A-Z\d]\b"
)
_PAN = re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")
_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
_PHONE = re.compile(r"\b(?:\+91[-\s]?)?[6-9]\d{9}\b|\b\+?1?\s?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b")
_INVOICE_ID = re.compile(r"\b[A-Z]{2,6}[-/]?\d{4,12}\b")
_PINCODE = re.compile(r"\b[1-9]\d{5}\b")

# Maps a regex → (label_prefix, confidence_boost)
_VALUE_PATTERNS: List[Tuple[re.Pattern, str, float]] = [
    (_GSTIN,      "GSTIN",    0.99),
    (_PAN,        "PAN",      0.97),
    (_EMAIL,      "Email",    0.95),
    (_PHONE,      "Phone",    0.90),
    (_DATE,       "Date",     0.88),
    (_AMOUNT,     "Amount",   0.85),
    (_INVOICE_ID, "Ref ID",   0.82),
    (_PINCODE,    "Pincode",  0.80),
]

# Colon-separated: "Key : Value" — key up to 60 chars, no newlines
_COLON_RE = re.compile(
    r"^([^\n:]{2,60}?)\s*:\s*(.+)$",
    re.MULTILINE,
)

# Label-like line: capitalised words, possibly with spaces and slashes
_LABEL_LINE = re.compile(r"^[A-Z][A-Za-z0-9 ./,\-()&]{2,60}$")

# Noisy / too-short value guard
_VALUE_NOISE = re.compile(r"^[-_.:,;|]+$")


@dataclass
class ExtractedField:
    field_key: str
    field_value: str
    confidence: float
    extraction_method: str
    page_number: int


class KeyValueExtractor:
    def extract(self, doc: ParsedDocument) -> List[ExtractedField]:
        fields: List[ExtractedField] = []
        seen: Dict[str, bool] = {}

        for block in doc.text_blocks:
            self._from_colon(block, fields, seen)
            self._from_multiline(block, fields, seen)
            self._from_regex_anchors(block, fields, seen)

        for table in doc.tables:
            self._from_table(table, fields, seen)

        return fields

    # ------------------------------------------------------------------
    # Strategy 1 — colon-separated "Key: Value"
    # ------------------------------------------------------------------

    def _from_colon(
        self, block: PageBlock, out: List[ExtractedField], seen: Dict[str, bool]
    ) -> None:
        for m in _COLON_RE.finditer(block.text):
            key = self._clean_key(m.group(1))
            val = self._clean_val(m.group(2))
            if not self._valid_pair(key, val):
                continue
            confidence = self._value_confidence(val, base=0.85)
            if block.via_ocr:
                confidence *= 0.9
            self._add(out, seen, key, val, confidence, "colon", block.page)

    # ------------------------------------------------------------------
    # Strategy 2 — label line followed by value line
    # ------------------------------------------------------------------

    def _from_multiline(
        self, block: PageBlock, out: List[ExtractedField], seen: Dict[str, bool]
    ) -> None:
        lines = [ln.strip() for ln in block.text.splitlines()]
        i = 0
        while i < len(lines) - 1:
            current = lines[i]
            nxt = lines[i + 1]
            if (
                _LABEL_LINE.match(current)
                and nxt
                and not _LABEL_LINE.match(nxt)
                and not _VALUE_NOISE.match(nxt)
                and len(nxt) <= 200
            ):
                key = self._clean_key(current)
                val = self._clean_val(nxt)
                if self._valid_pair(key, val):
                    confidence = self._value_confidence(val, base=0.72)
                    self._add(out, seen, key, val, confidence, "multiline", block.page)
                    i += 2
                    continue
            i += 1

    # ------------------------------------------------------------------
    # Strategy 3 — regex anchor scan (GSTIN, PAN, dates, amounts …)
    # ------------------------------------------------------------------

    def _from_regex_anchors(
        self, block: PageBlock, out: List[ExtractedField], seen: Dict[str, bool]
    ) -> None:
        for pattern, label, confidence in _VALUE_PATTERNS:
            for m in pattern.finditer(block.text):
                val = m.group(0).strip()
                # Try to find a key on the same line before the match
                line_start = block.text.rfind("\n", 0, m.start()) + 1
                prefix = block.text[line_start : m.start()].strip().rstrip(":- ")
                key = self._clean_key(prefix) if prefix else label
                if not key:
                    key = label
                self._add(out, seen, key, val, confidence, "regex", block.page)

    # ------------------------------------------------------------------
    # Strategy 4 — table extraction
    # ------------------------------------------------------------------

    def _from_table(
        self, table: TableBlock, out: List[ExtractedField], seen: Dict[str, bool]
    ) -> None:
        rows = [r for r in table.rows if any(c for c in r)]
        if not rows:
            return

        # Two-column key/value table
        if all(len(r) == 2 for r in rows):
            for row in rows:
                key = self._clean_key(row[0] or "")
                val = self._clean_val(row[1] or "")
                if self._valid_pair(key, val):
                    confidence = self._value_confidence(val, base=0.88)
                    self._add(out, seen, key, val, confidence, "table", table.page)
            return

        # Multi-column: first non-empty row = headers
        headers: Optional[List[str]] = None
        for row in rows:
            non_empty = [c for c in row if c]
            if len(non_empty) >= 2:
                headers = [str(c).strip() if c else f"col_{i}" for i, c in enumerate(row)]
                data_rows = rows[rows.index(row) + 1 :]
                break
        else:
            return

        for row in data_rows:
            for j, cell in enumerate(row):
                if j >= len(headers) or not cell:
                    continue
                key = self._clean_key(headers[j])
                val = self._clean_val(str(cell))
                if self._valid_pair(key, val):
                    confidence = self._value_confidence(val, base=0.80)
                    self._add(out, seen, key, val, confidence, "table", table.page)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _add(
        self,
        out: List[ExtractedField],
        seen: Dict[str, bool],
        key: str,
        val: str,
        confidence: float,
        method: str,
        page: int,
    ) -> None:
        dedup_key = key.lower().strip()
        if dedup_key in seen:
            return
        seen[dedup_key] = True
        out.append(
            ExtractedField(
                field_key=key,
                field_value=val,
                confidence=round(confidence, 4),
                extraction_method=method,
                page_number=page,
            )
        )

    @staticmethod
    def _clean_key(raw: str) -> str:
        key = re.sub(r"[_\-]+", " ", raw)
        key = re.sub(r"\s+", " ", key).strip().rstrip(":.,-")
        return key[:200]

    @staticmethod
    def _clean_val(raw: str) -> str:
        return re.sub(r"\s+", " ", raw).strip()[:500]

    @staticmethod
    def _valid_pair(key: str, val: str) -> bool:
        if not key or not val:
            return False
        if len(key) < 2 or len(val) < 1:
            return False
        if _VALUE_NOISE.match(val):
            return False
        # Skip pure-numeric keys (likely row indices)
        if re.match(r"^\d+$", key):
            return False
        return True

    @staticmethod
    def _value_confidence(value: str, base: float) -> float:
        """Boost confidence when value matches a known high-value pattern."""
        for pattern, _, boost in _VALUE_PATTERNS:
            if pattern.search(value):
                return min(boost, 0.99)
        return base
