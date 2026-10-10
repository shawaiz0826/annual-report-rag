"""LLM document profiling: discover what a document is at ingest time."""

from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, Field

from core.agent.prompts import load_prompt
from core.config import IngestionConfig
from core.llm.client import LLMProvider, structured
from core.models import DocType, DocumentProfile, EntityType, PageText, ReportingUnit, Usage

log = logging.getLogger(__name__)


class ProfilerOutput(BaseModel):
    """The JSON the profiler model must return (a subset of :class:`DocumentProfile`)."""

    doc_type: DocType
    legal_entity: str | None = None
    entity_type: EntityType | None = None
    parent_organisation: str | None = None
    issuer: str | None = None
    is_primary_issuer_report: bool = False
    fiscal_year: int | None = None
    period_end: str | None = None
    reporting_currency: str | None = None
    reporting_unit: ReportingUnit | None = None
    sector: str | None = None
    country: str | None = None
    language: str = "en"
    summary: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)


def document_id_for(path: Path) -> str:
    """sha256 of the file bytes, first 16 hex characters."""
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()[:16]


_UNIT_WORDS = {"thousand": "thousands", "million": "millions", "billion": "billions"}
_UNIT_DECLARATION = re.compile(
    r"\b(?:in|of)\s+(?:[€$£]|EUR|USD|GBP|euro|dollars?|pounds?)?\s*(thousand|million|billion)s?\b"
    r"|\((?:in\s+)?(thousand|million|billion)s?\b[^)]{0,60}\)"
    r"|[€$£]\s*(thousand|million|billion)s?\b",
    re.IGNORECASE,
)


def detect_unit_declarations(pages: list[PageText]) -> list[tuple[str, int, str, int]]:
    """Find phrases such as ``"in € million"`` / ``"(in thousands)"`` across the document.

    Returns ``[(unit, count, example_snippet, first_page), ...]`` sorted by count, where
    ``unit`` is one of ``thousands|millions|billions``. Used both as evidence for the
    profiler and as a generic cross-check of its answer.
    """
    counts: Counter[str] = Counter()
    example: dict[str, tuple[str, int]] = {}
    for page in pages:
        for match in _UNIT_DECLARATION.finditer(page.text):
            word = next(g for g in match.groups() if g).lower()
            unit = _UNIT_WORDS[word]
            counts[unit] += 1
            if unit not in example:
                start, end = max(0, match.start() - 30), min(len(page.text), match.end() + 30)
                example[unit] = (page.text[start:end].replace("\n", " "), page.page)
    return [(u, n, example[u][0], example[u][1]) for u, n in counts.most_common()]


def select_profile_pages(pages: list[PageText], config: IngestionConfig) -> list[PageText]:
    """First ``profile_pages`` pages, one mid-document page, and the first statements page.

    The statements page is the first page carrying a unit declaration — that is where the
    reporting currency and unit are stated, which the opening pages often omit.
    """
    chosen = pages[: config.profile_pages]
    if config.profile_sample_middle and len(pages) > config.profile_pages + 1:
        middle = pages[len(pages) // 2]
        if middle not in chosen:
            chosen = [*chosen, middle]
    declarations = detect_unit_declarations(pages)
    if declarations:
        first_page = min(d[3] for d in declarations)
        statements = next((p for p in pages if p.page == first_page), None)
        if statements is not None and statements not in chosen:
            chosen = [*chosen, statements]
    return sorted(chosen, key=lambda p: p.page)


def build_profiler_messages(
    file_name: str,
    page_count: int,
    sample: list[PageText],
    config: IngestionConfig,
    sample_source: list[PageText] | None = None,
) -> list[dict[str, str]]:
    """Compose the profiler prompt (``sample_source`` = all pages, for unit evidence)."""
    _, system = load_prompt("profiler")
    parts = [f"File name (weak hint): {file_name}", f"Total pages: {page_count}"]
    declarations = detect_unit_declarations(sample_source or sample)
    if declarations:
        parts.append(
            "Unit declarations found across the whole document (unit: occurrences, example):"
        )
        parts += [
            f'  - {u}: {n} occurrence(s), e.g. p.{pg}: "...{ex}..."'
            for u, n, ex, pg in declarations[:4]
        ]
    parts.append("")
    for page in sample:
        text = page.text[: config.profile_max_chars_per_page]
        parts.append(f"===== PAGE {page.page} =====\n{text}\n")
    return [{"role": "system", "content": system}, {"role": "user", "content": "\n".join(parts)}]


class DocumentProfiler:
    """Turns cleaned pages into a :class:`DocumentProfile` via the LLM."""

    def __init__(self, provider: LLMProvider, config: IngestionConfig, model: str | None = None):
        self.provider = provider
        self.config = config
        self.model = model

    def profile(
        self, path: Path, document_id: str, pages: list[PageText], page_count: int
    ) -> tuple[DocumentProfile, Usage]:
        """Profile one document; applies generic validation rules after the model answers."""
        sample = select_profile_pages(pages, self.config)
        messages = build_profiler_messages(path.name, page_count, sample, self.config, pages)
        version, _ = load_prompt("profiler")
        output, usage, answered_by = structured(
            self.provider,
            messages,
            ProfilerOutput,
            schema_name="document_profile",
            model=self.model,
            max_tokens=4000,
        )
        profile = DocumentProfile(
            document_id=document_id,
            source_path=str(path),
            file_name=path.name,
            page_count=page_count,
            profiler_confidence=output.confidence,
            profiler_model=answered_by,
            profiler_prompt_version=version,
            **output.model_dump(exclude={"confidence"}),
        )
        return self._validate(profile, pages), usage

    @staticmethod
    def _validate(profile: DocumentProfile, pages: list[PageText]) -> DocumentProfile:
        """Generic consistency rules: lower confidence and warn rather than fail."""
        warnings: list[str] = []
        if profile.is_financial:
            declared = detect_unit_declarations(pages)
            if declared and profile.reporting_unit not in {d[0] for d in declared}:
                dominant = declared[0][0]
                warnings.append(
                    f"reporting_unit '{profile.reporting_unit}' not declared in text; "
                    f"using dominant declaration '{dominant}' ({declared[0][1]}x)"
                )
                profile.reporting_unit = dominant
            if profile.fiscal_year is None:
                warnings.append("financial document without fiscal_year")
            if not profile.reporting_currency:
                warnings.append("financial document without reporting_currency")
            if not profile.legal_entity:
                warnings.append("financial document without legal_entity")
            if not profile.issuer:
                warnings.append("financial document without issuer")
            if profile.entity_type == "group" and not profile.is_primary_issuer_report:
                profile.is_primary_issuer_report = True
            if profile.entity_type == "subsidiary" and profile.is_primary_issuer_report:
                profile.is_primary_issuer_report = False
                warnings.append("subsidiary flagged as primary report; corrected")
        else:
            if profile.issuer or profile.legal_entity:
                warnings.append("non-financial document carried issuer/legal_entity; cleared")
            profile.issuer = None
            profile.legal_entity = None
            profile.entity_type = None
            profile.parent_organisation = None
            profile.is_primary_issuer_report = False
        if profile.issuer:
            profile.issuer = profile.issuer.strip()
        if warnings:
            profile.profiler_confidence = round(profile.profiler_confidence * 0.7, 3)
            profile.warnings.extend(warnings)
            log.warning("%s: %s", profile.file_name, "; ".join(warnings))
        return profile
