"""Loader protocol and registry.

A loader turns a file into a list of :class:`~core.models.PageText`. The registry maps
file extensions to loaders so the pipeline never branches on file type itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable

from core.models import PageText


@runtime_checkable
class DocumentLoader(Protocol):
    """Anything that can read a file into pages."""

    extensions: tuple[str, ...]

    def load(self, path: Path) -> list[PageText]:
        """Read ``path`` and return its pages (1-based page numbers)."""
        ...


class LoaderRegistry:
    """Extension → loader lookup."""

    def __init__(self, loaders: list[DocumentLoader] | None = None) -> None:
        self._by_ext: dict[str, DocumentLoader] = {}
        for loader in loaders or []:
            self.register(loader)

    def register(self, loader: DocumentLoader) -> None:
        """Register ``loader`` for each of its extensions (lower-case, with dot)."""
        for ext in loader.extensions:
            self._by_ext[ext.lower()] = loader

    def get(self, extension: str) -> DocumentLoader:
        """Return the loader for ``extension`` or raise ``KeyError``."""
        ext = extension.lower()
        if not ext.startswith("."):
            ext = "." + ext
        try:
            return self._by_ext[ext]
        except KeyError as exc:
            raise KeyError(f"No loader registered for {ext!r}") from exc

    def supports(self, path: Path) -> bool:
        """True if a loader exists for ``path``'s extension."""
        return path.suffix.lower() in self._by_ext

    @property
    def extensions(self) -> list[str]:
        """All supported extensions."""
        return sorted(self._by_ext)
