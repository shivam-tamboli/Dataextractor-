"""
LLM-powered key-value extraction using Grok (xAI).

Sends the raw PDF text to Grok and asks it to return a structured JSON
list of key-value pairs. Results are merged with regex extraction —
LLM fields are tagged with extraction_method="llm" and confidence=0.97.
"""
import json
import logging
from typing import List

from openai import AsyncOpenAI

from app.config import get_settings
from app.services.extractor import ExtractedField

logger = logging.getLogger(__name__)
settings = get_settings()

_SYSTEM_PROMPT = """You are a precise document data extraction engine.

Given raw text extracted from a PDF document, extract every key-value pair you can find.
Return ONLY a valid JSON array — no explanation, no markdown, no code fences.

Each item in the array must have exactly these fields:
  "key"   : the field label (string)
  "value" : the field value (string)

Rules:
- Include ALL data fields: dates, IDs, names, addresses, amounts, tax numbers, etc.
- Do not skip any field, even if you are unsure
- Normalize keys to Title Case (e.g. "invoice number", "INVOICE NO" → "Invoice Number")
- Keep values exactly as they appear in the document
- If the document has a table, extract each cell as a key-value pair using the column header as key
- Return an empty array [] if no fields are found

Example output:
[
  {"key": "Invoice Number", "value": "INV-2024-001"},
  {"key": "Date", "value": "08-Feb-2024"},
  {"key": "GSTIN", "value": "24AAICG5558N1Z2"},
  {"key": "Total Amount", "value": "INR 390,273.00"}
]"""


class LLMExtractor:
    def __init__(self):
        self._client = AsyncOpenAI(
            api_key=settings.xai_api_key,
            base_url="https://api.x.ai/v1",
        )

    async def extract(self, text: str, page: int = 1) -> List[ExtractedField]:
        if not settings.xai_api_key:
            logger.warning("XAI_API_KEY not set — skipping LLM extraction")
            return []

        if not text.strip():
            return []

        try:
            response = await self._client.chat.completions.create(
                model=settings.xai_model,
                messages=[
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": f"Extract all key-value pairs from this document:\n\n{text[:12000]}"},
                ],
                temperature=0,
                max_tokens=4096,
            )

            raw = response.choices[0].message.content.strip()
            logger.debug("Grok raw response: %s", raw[:500])

            pairs = json.loads(raw)
            if not isinstance(pairs, list):
                logger.warning("Grok returned non-list JSON: %s", type(pairs))
                return []

            fields = []
            for item in pairs:
                key = str(item.get("key", "")).strip()
                value = str(item.get("value", "")).strip()
                if key and value:
                    fields.append(
                        ExtractedField(
                            field_key=key,
                            field_value=value,
                            confidence=0.97,
                            extraction_method="llm",
                            page_number=page,
                        )
                    )

            logger.info("Grok extracted %d fields from page %d", len(fields), page)
            return fields

        except json.JSONDecodeError as exc:
            logger.error("Grok returned invalid JSON: %s", exc)
            return []
        except Exception as exc:
            logger.error("Grok API call failed: %s", exc)
            return []
