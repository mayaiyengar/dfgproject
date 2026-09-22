"""Base class for sources whose underlying documents are PDFs (architecture.md
§3.2) — the dominant format for actual rule/order text across the state layer
even where the index page is HTML (source-inventory.md §5, risks-and-
limitations.md §5).
"""

from __future__ import annotations

import io

import httpx
import pdfplumber

from ingestion.base.adapter import SourceAdapter
from utils.http import build_http_client, request_with_retry


class PdfDocumentAdapter(SourceAdapter):
    base_url: str

    def __init__(self, *, client: httpx.Client | None = None) -> None:
        self._client = client or build_http_client(base_url=self.base_url)
        self._owns_client = client is None

    def _download_pdf(self, url: str) -> bytes:
        response = request_with_retry(self._client, "GET", url)
        return response.content

    def _extract_text(self, pdf_bytes: bytes) -> str:
        """Best-effort text extraction. Extraction quality varies by PDF
        layout (risks-and-limitations.md §5) — callers should treat the
        result as noisy, not authoritative; `official_text_url` (not this
        extracted text) remains the source of truth for the actual document.
        """
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages_text = [page.extract_text() or "" for page in pdf.pages]
        return "\n".join(pages_text)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()
