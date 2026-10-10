"""Default loader registry wiring."""

from __future__ import annotations

from core.ingestion.loaders.base import LoaderRegistry
from core.ingestion.loaders.docx import DocxLoader
from core.ingestion.loaders.pdf import PdfLoader
from core.ingestion.loaders.text import TextLoader


def default_registry() -> LoaderRegistry:
    """Registry with the built-in PDF, DOCX and text loaders."""
    return LoaderRegistry([PdfLoader(), DocxLoader(), TextLoader()])
