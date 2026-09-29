"""Shared limits and pre-parse guards for document text extraction."""

from __future__ import annotations

import os
import zipfile
from io import BytesIO

MAX_EXTRACT_CHARS = int(os.environ.get("KB_MAX_EXTRACT_CHARS", str(2_000_000)))
MAX_DOCX_PARTS = int(os.environ.get("KB_MAX_DOCX_PARTS", "50000"))
MAX_PDF_PAGES = int(os.environ.get("KB_MAX_PDF_PAGES", "500"))
MAX_ZIP_MEMBERS = int(os.environ.get("KB_MAX_ZIP_MEMBERS", "200"))
MAX_ZIP_UNCOMPRESSED_BYTES = int(
    os.environ.get("KB_MAX_ZIP_UNCOMPRESSED_BYTES", str(100 * 1024 * 1024))
)


def cap_extracted_text(text: str) -> str:
    if len(text) <= MAX_EXTRACT_CHARS:
        return text
    return text[:MAX_EXTRACT_CHARS] + "\n\n[Text extraction truncated]"


def validate_zip_archive(data: bytes) -> tuple[bool, str]:
    """Reject zip bombs before expensive parsing (DOCX/XLSX are ZIP files)."""
    try:
        with zipfile.ZipFile(BytesIO(data)) as zf:
            cumulative = 0
            count = 0
            for info in zf.infolist():
                if info.is_dir():
                    continue
                count += 1
                if count > MAX_ZIP_MEMBERS:
                    return False, f"too many archive members ({count} > {MAX_ZIP_MEMBERS})"
                cumulative += info.file_size
                if cumulative > MAX_ZIP_UNCOMPRESSED_BYTES:
                    return False, (
                        f"cumulative uncompressed size exceeds "
                        f"{MAX_ZIP_UNCOMPRESSED_BYTES // (1024 * 1024)}MB limit"
                    )
                resolved = os.path.normpath(info.filename)
                if resolved.startswith("..") or os.path.isabs(resolved):
                    return False, f"path traversal detected in member: {info.filename!r}"
    except zipfile.BadZipFile:
        return False, "invalid zip archive"
    except Exception:
        return False, "zip validation failed"
    return True, ""


def validate_pdf_for_extraction(data: bytes) -> tuple[bool, str]:
    """Cheap structural check before full PDF text extraction."""
    try:
        from pdfminer.pdfparser import PDFParser
        from pdfminer.pdfdocument import PDFDocument
        from pdfminer.pdfpage import PDFPage

        parser = PDFParser(BytesIO(data))
        document = PDFDocument(parser)
        page_count = sum(1 for _ in PDFPage.create_pages(document))
        if page_count > MAX_PDF_PAGES:
            return False, f"too many PDF pages ({page_count} > {MAX_PDF_PAGES})"
    except Exception:
        return False, "invalid or unsupported PDF"
    return True, ""
