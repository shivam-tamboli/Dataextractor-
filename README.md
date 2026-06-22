# PDF Document Data Extractor

A production-ready API that accepts **any PDF document** — invoices, tax forms, receipts, contracts — and returns all data as structured key-value pairs, stored in PostgreSQL. No external LLM API is used.

---

## Architecture

```
┌─────────────┐   POST /upload   ┌──────────────────┐
│   Client    │ ──────────────▶  │   FastAPI Route   │
└─────────────┘                  │  (returns doc_id) │
                                 └────────┬─────────┘
                                          │ BackgroundTasks
                                          ▼
                                 ┌──────────────────┐
                                 │  Worker Process   │
                                 │  ┌─────────────┐ │
                                 │  │  PDFParser  │ │  ← pdfplumber (digital)
                                 │  │  (OCR fall- │ │  ← pytesseract (scanned)
                                 │  │   back)     │ │
                                 │  └──────┬──────┘ │
                                 │         ▼        │
                                 │  ┌─────────────┐ │
                                 │  │  KV Extract │ │  ← regex + pattern matching
                                 │  └──────┬──────┘ │
                                 └─────────┼────────┘
                                           ▼
                                  ┌─────────────────┐
                                  │   PostgreSQL     │
                                  │  documents       │
                                  │  document_fields │
                                  └─────────────────┘
```

### Extraction pipeline (no LLM)

| Strategy | How it works | Example match |
|---|---|---|
| **Colon-separated** | Regex `KEY: VALUE` on same line | `Invoice No: INV-2024-001` |
| **Multi-line** | Label on one line, value on next | `GSTIN\n24AAICG5558N1Z2` |
| **Table rows** | pdfplumber table extraction | 2-col key/value or header+data |
| **Regex anchors** | GSTIN / PAN / date / amount / email / phone patterns | `24AAICG5558N1Z2` |
| **OCR fallback** | pytesseract on scanned pages, then same strategies | Any scanned invoice |

---

## Database Schema

### `documents`
| Column | Type | Description |
|---|---|---|
| id | integer PK | Auto-increment |
| file_name | varchar | Original filename |
| file_size | integer | Bytes |
| status | enum | `pending` → `processing` → `done` / `failed` |
| page_count | integer | Total pages |
| is_scanned | integer | 1 if OCR was needed |
| error_message | text | Set on failure |
| created_at | timestamp | Upload time |

### `document_fields`
| Column | Type | Description |
|---|---|---|
| id | integer PK | Auto-increment |
| document_id | integer FK | References documents.id |
| field_key | varchar | Extracted label |
| field_value | text | Extracted value |
| confidence | float | 0–1 confidence score |
| extraction_method | varchar | `colon` / `multiline` / `table` / `regex` |
| page_number | integer | Source page |

---

## API Reference

### `POST /v1/documents/upload`
Upload a PDF for extraction.

**Request:** `multipart/form-data` with field `file` (PDF).

**Response `202`:**
```json
{
  "document_id": 42,
  "status": "pending",
  "message": "Document accepted. Extraction is running in the background."
}
```

---

### `GET /v1/documents/{id}`
Retrieve a document and all extracted fields.

**Response `200`:**
```json
{
  "id": 42,
  "file_name": "invoice.pdf",
  "status": "done",
  "page_count": 2,
  "is_scanned": 0,
  "fields": [
    {
      "id": 1,
      "field_key": "Invoice No",
      "field_value": "GGLSUK022305172",
      "confidence": 0.85,
      "extraction_method": "colon",
      "page_number": 1
    },
    {
      "id": 2,
      "field_key": "Date",
      "field_value": "08-Feb-2024",
      "confidence": 0.88,
      "extraction_method": "colon",
      "page_number": 1
    },
    {
      "id": 3,
      "field_key": "GSTIN",
      "field_value": "24AAICG5558N1Z2",
      "confidence": 0.99,
      "extraction_method": "regex",
      "page_number": 1
    }
  ]
}
```

---

### `GET /v1/documents?skip=0&limit=50`
List all documents (most recent first).

---

### `GET /health`
Health check — returns `{"status": "ok"}`.

---

## Quick Start (Docker — recommended)

```bash
# 1. Clone and enter the repo
git clone <repo-url>
cd Dataextracor

# 2. Start everything
docker compose up --build

# 3. Upload a PDF
curl -X POST http://localhost:8000/v1/documents/upload \
  -F "file=@your-invoice.pdf"
# → { "document_id": 1, "status": "pending", ... }

# 4. Poll until done
curl http://localhost:8000/v1/documents/1

# 5. Interactive docs
open http://localhost:8000/docs
```

---

## Local Development (without Docker)

### Prerequisites

- Python 3.11+
- PostgreSQL 14+
- Tesseract OCR

```bash
# macOS
brew install tesseract postgresql@16

# Ubuntu / Debian
sudo apt install tesseract-ocr libpq-dev
```

### Setup

```bash
# 1. Create virtualenv
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment
cp .env.example .env
# Edit .env — set DATABASE_URL and TESSERACT_CMD

# 4. Create database
createdb dataextractor

# 5. Run migrations
alembic upgrade head

# 6. Start API
uvicorn app.main:app --reload
```

The API is now at `http://localhost:8000` and interactive docs at `http://localhost:8000/docs`.

---

## Project Structure

```
Dataextracor/
├── app/
│   ├── main.py            # FastAPI app factory, lifespan, middleware
│   ├── config.py          # Pydantic settings (env vars)
│   ├── schemas.py         # Pydantic I/O models
│   ├── api/
│   │   └── documents.py   # Route handlers
│   ├── db/
│   │   └── session.py     # Async engine, session factory, init_db
│   ├── models/
│   │   └── database.py    # SQLAlchemy ORM models
│   └── services/
│       ├── pdf_parser.py  # pdfplumber + pytesseract
│       ├── extractor.py   # Key-value extraction strategies
│       └── worker.py      # Background processing orchestrator
├── alembic/               # Database migrations
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── .env.example
```

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://postgres:postgres@localhost:5432/dataextractor` | PostgreSQL connection |
| `UPLOAD_DIR` | `/tmp/pdf_uploads` | Temp directory for uploaded files |
| `MAX_FILE_SIZE_MB` | `50` | Upload size limit |
| `TESSERACT_CMD` | `/usr/bin/tesseract` | Path to Tesseract binary |
| `DEBUG` | `false` | Enable SQL echo and debug logging |

---

## Running Tests

```bash
pip install pytest pytest-asyncio httpx
pytest tests/ -v
```
