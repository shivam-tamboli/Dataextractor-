import logging
import os
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.session import AsyncSessionLocal, get_db
from app.models.database import Document, DocumentStatus
from app.schemas import DocumentListItem, DocumentOut, UploadResponse
from app.services.worker import process_document

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/v1/documents", tags=["documents"])

_MAX_BYTES = settings.max_file_size_mb * 1024 * 1024


# ---------------------------------------------------------------------------
# POST /v1/documents/upload
# ---------------------------------------------------------------------------


@router.post("/upload", response_model=UploadResponse, status_code=202)
async def upload_document(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        # Be lenient — some clients send octet-stream for PDFs
        if not (file.filename or "").lower().endswith(".pdf"):
            raise HTTPException(
                status_code=415,
                detail="Only PDF files are accepted.",
            )

    file_bytes = await file.read()

    if len(file_bytes) > _MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds the {settings.max_file_size_mb} MB limit.",
        )

    # Persist a record immediately so the caller gets an ID
    doc = Document(
        file_name=file.filename or "upload.pdf",
        file_size=len(file_bytes),
        status=DocumentStatus.PENDING,
    )
    db.add(doc)
    await db.commit()
    await db.refresh(doc)

    # Write to a temp file that the worker will read and then delete
    os.makedirs(settings.upload_dir, exist_ok=True)
    tmp_path = os.path.join(settings.upload_dir, f"{uuid.uuid4()}.pdf")
    with open(tmp_path, "wb") as fh:
        fh.write(file_bytes)

    # Hand off to background; give the worker its own DB session
    worker_session = AsyncSessionLocal()
    background_tasks.add_task(process_document, doc.id, tmp_path, worker_session)

    logger.info("Accepted document_id=%d file=%s", doc.id, doc.file_name)

    return UploadResponse(
        document_id=doc.id,
        status=doc.status.value,
        message="Document accepted. Extraction is running in the background.",
    )


# ---------------------------------------------------------------------------
# GET /v1/documents/{id}
# ---------------------------------------------------------------------------


@router.get("/{document_id}", response_model=DocumentOut)
async def get_document(
    document_id: int,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Document).where(Document.id == document_id)
    )
    doc = result.scalar_one_or_none()

    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    return doc


# ---------------------------------------------------------------------------
# GET /v1/documents
# ---------------------------------------------------------------------------


@router.get("", response_model=list[DocumentListItem])
async def list_documents(
    skip: int = 0,
    limit: int = 50,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Document)
        .order_by(Document.created_at.desc())
        .offset(skip)
        .limit(min(limit, 200))
    )
    docs = result.scalars().all()
    return docs
