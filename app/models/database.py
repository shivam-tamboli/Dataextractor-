import enum
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import relationship

from app.db.session import Base


class DocumentStatus(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class Document(Base):
    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, index=True)
    file_name = Column(String(255), nullable=False)
    file_size = Column(Integer, nullable=True)
    status = Column(
        Enum(DocumentStatus, values_callable=lambda x: [e.value for e in x]),
        default=DocumentStatus.PENDING,
        nullable=False,
        index=True,
    )
    page_count = Column(Integer, nullable=True)
    is_scanned = Column(Integer, default=0)  # 0/1 flag
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    fields = relationship(
        "DocumentField",
        back_populates="document",
        cascade="all, delete-orphan",
        lazy="selectin",
    )


class DocumentField(Base):
    __tablename__ = "document_fields"

    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(
        Integer, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    field_key = Column(String(500), nullable=False)
    field_value = Column(Text, nullable=False)
    confidence = Column(Float, default=1.0, nullable=False)
    extraction_method = Column(String(50), nullable=True)  # colon/multiline/table/ocr
    page_number = Column(Integer, nullable=True)

    document = relationship("Document", back_populates="fields")
