from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class DocumentFieldOut(BaseModel):
    id: int
    field_key: str
    field_value: str
    confidence: float
    extraction_method: Optional[str] = None
    page_number: Optional[int] = None

    model_config = {"from_attributes": True}


class DocumentOut(BaseModel):
    id: int
    file_name: str
    file_size: Optional[int] = None
    status: str
    page_count: Optional[int] = None
    is_scanned: Optional[int] = None
    error_message: Optional[str] = None
    created_at: datetime
    fields: List[DocumentFieldOut] = []

    model_config = {"from_attributes": True}


class DocumentListItem(BaseModel):
    id: int
    file_name: str
    status: str
    page_count: Optional[int] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class UploadResponse(BaseModel):
    document_id: int
    status: str
    message: str
