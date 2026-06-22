"""
Background extraction worker.

Flow: read file → parse PDF → regex extraction + Grok LLM extraction (parallel)
      → merge results → persist → update status.
"""
import asyncio
import logging
import os
from typing import List

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.database import Document, DocumentField, DocumentStatus
from app.services.extractor import ExtractedField, KeyValueExtractor
from app.services.llm_extractor import LLMExtractor
from app.services.pdf_parser import PDFParser

logger = logging.getLogger(__name__)

_parser = PDFParser()
_extractor = KeyValueExtractor()
_llm = LLMExtractor()


async def process_document(document_id: int, file_path: str, db: AsyncSession) -> None:
    logger.info("Worker starting for document_id=%d", document_id)

    try:
        await _set_status(db, document_id, DocumentStatus.PROCESSING)

        file_bytes = _read_file(file_path)
        parsed = _parser.parse(file_bytes)

        # Build full text for the LLM (all pages joined)
        full_text = "\n\n".join(b.text for b in parsed.text_blocks)

        # Run regex extraction (sync) and LLM extraction (async) together
        regex_fields = _extractor.extract(parsed)
        llm_fields = await _llm.extract(full_text, page=1)

        # Merge: LLM fields take precedence (higher confidence), regex fills gaps
        merged = _merge(regex_fields, llm_fields)

        await _persist_fields(db, document_id, merged)
        await _set_status(
            db,
            document_id,
            DocumentStatus.DONE,
            page_count=parsed.page_count,
            is_scanned=int(parsed.is_scanned),
        )
        logger.info(
            "Worker done for document_id=%d — %d fields (%d regex + %d llm)",
            document_id,
            len(merged),
            len(regex_fields),
            len(llm_fields),
        )

    except Exception as exc:
        logger.exception("Worker failed for document_id=%d: %s", document_id, exc)
        await _set_status(db, document_id, DocumentStatus.FAILED, error_message=str(exc))
    finally:
        _cleanup_file(file_path)
        await db.close()


# ---------------------------------------------------------------------------
# Merge strategy
# ---------------------------------------------------------------------------

def _merge(
    regex_fields: List[ExtractedField],
    llm_fields: List[ExtractedField],
) -> List[ExtractedField]:
    """
    LLM fields win on key conflicts (higher confidence).
    Regex-only fields are appended afterwards.
    """
    seen = {}

    # Index LLM results first
    for f in llm_fields:
        seen[f.field_key.lower().strip()] = f

    # Add regex results only if LLM didn't already extract that key
    for f in regex_fields:
        k = f.field_key.lower().strip()
        if k not in seen:
            seen[k] = f

    return list(seen.values())


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_file(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def _cleanup_file(path: str) -> None:
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError as exc:
        logger.warning("Could not delete temp file %s: %s", path, exc)


async def _set_status(
    db: AsyncSession,
    document_id: int,
    status: DocumentStatus,
    *,
    page_count: int | None = None,
    is_scanned: int | None = None,
    error_message: str | None = None,
) -> None:
    from sqlalchemy import select

    result = await db.execute(select(Document).where(Document.id == document_id))
    doc: Document | None = result.scalar_one_or_none()
    if doc is None:
        logger.error("Document %d not found — cannot update status", document_id)
        return

    doc.status = status
    if page_count is not None:
        doc.page_count = page_count
    if is_scanned is not None:
        doc.is_scanned = is_scanned
    if error_message is not None:
        doc.error_message = error_message[:1000]

    await db.commit()


async def _persist_fields(
    db: AsyncSession, document_id: int, fields: List[ExtractedField]
) -> None:
    if not fields:
        return

    db_fields = [
        DocumentField(
            document_id=document_id,
            field_key=f.field_key,
            field_value=f.field_value,
            confidence=f.confidence,
            extraction_method=f.extraction_method,
            page_number=f.page_number,
        )
        for f in fields
    ]
    db.add_all(db_fields)
    await db.commit()
