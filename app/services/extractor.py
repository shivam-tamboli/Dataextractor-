"""
Key-value extraction from parsed PDF content using Groq LLM.
"""
import logging
import json
import urllib.request
import urllib.error
from dataclasses import dataclass
from typing import List

from app.services.pdf_parser import ParsedDocument

logger = logging.getLogger(__name__)

@dataclass
class ExtractedField:
    field_key: str
    field_value: str
    confidence: float
    extraction_method: str
    page_number: int


class KeyValueExtractor:
    def __init__(self):
        self.api_key = "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"
        self.api_url = "https://api.groq.com/openai/v1/chat/completions"

    def extract(self, doc: ParsedDocument) -> List[ExtractedField]:
        fields: List[ExtractedField] = []
        
        # Combine all text from the document
        full_text = ""
        for block in doc.text_blocks:
            full_text += f"--- Page {block.page} ---\n{block.text}\n\n"
            
        for table in doc.tables:
            full_text += f"--- Table on Page {table.page} ---\n"
            for row in table.rows:
                full_text += " | ".join(str(item) for item in row if item is not None) + "\n"
            full_text += "\n"

        if not full_text.strip():
            return fields

        prompt = (
            "You are a highly accurate data extraction assistant. Extract all relevant key-value pairs "
            "from the following document text. Return ONLY a valid JSON object where keys are the field names "
            "and values are the corresponding extracted values. Do not wrap in markdown blocks, do not include any other text, just return raw JSON.\n\n"
            f"Document Text:\n{full_text}"
        )

        data = {
            "model": "llama3-70b-8192",
            "messages": [
                {"role": "system", "content": "You output only raw valid JSON. No markdown formatting."},
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.0
        }

        req = urllib.request.Request(
            self.api_url, 
            data=json.dumps(data).encode("utf-8"), 
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
        )

        try:
            with urllib.request.urlopen(req) as response:
                result = json.loads(response.read().decode("utf-8"))
                content = result["choices"][0]["message"]["content"]
                
                # Cleanup potential formatting
                content = content.strip()
                if content.startswith("```json"):
                    content = content[7:]
                if content.startswith("```"):
                    content = content[3:]
                if content.endswith("```"):
                    content = content[:-3]
                
                extracted_data = json.loads(content.strip())
                
                if isinstance(extracted_data, dict):
                    for k, v in extracted_data.items():
                        # Handle if LLM returned a nested structure
                        if isinstance(v, (dict, list)):
                            v = json.dumps(v)
                        else:
                            v = str(v)
                            
                        # Avoid empty values
                        if k.strip() and v.strip():
                            fields.append(
                                ExtractedField(
                                    field_key=k.strip(),
                                    field_value=v.strip(),
                                    confidence=0.99,
                                    extraction_method="llm-groq",
                                    page_number=1
                                )
                            )
        except Exception as e:
            logger.error(f"Failed to extract using LLM: {e}")

        return fields
