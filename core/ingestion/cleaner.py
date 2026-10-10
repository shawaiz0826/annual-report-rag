"""Text cleaning — pure functions, unit-tested, no I/O.

Order of operations in :func:`clean_pages`:

1. Unicode normalisation (NFKC), ligature repair, whitespace collapse (newlines kept).
2. Within-page near-duplicate line removal.
3. Running header/footer removal (lines recurring on a large share of pages, with
   digits masked so page numbers do not defeat the match).
4. Dropping pages that end up (almost) empty unless they carry tables.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

from core.models import PageText

_LIGATURES = {
    "ﬀ": "ff",
    "ﬁ": "fi",
    "ﬂ": "fl",
    "ﬃ": "ffi",
    "ﬄ": "ffl",
    "’": "'",
    "‘": "'",
    "“": '"',
    "”": '"',
    " ": " ",
}
_MULTISPACE = re.compile(r"[ \t  ]+")
_DIGITS = re.compile(r"\d")


@dataclass(frozen=True)
class CleaningOptions:
    """Tunables for :func:`clean_pages` (mirrors ``config.yaml: ingestion``)."""

    dedupe_lines: bool = True
    dedupe_window: int = 3
    header_footer_min_page_ratio: float = 0.5
    header_footer_long_line_ratio: float = 0.15
    header_footer_long_line_chars: int = 25
    min_pages_for_header_detection: int = 4
    header_footer_zone_lines: int = 3
    min_chunk_chars: int = 200


# --------------------------------------------------------------------------- normalisation


def normalise_text(text: str) -> str:
    """NFKC-normalise, repair ligatures/quotes, collapse runs of spaces, trim line ends."""
    text = unicodedata.normalize("NFKC", text)
    for src, dst in _LIGATURES.items():
        text = text.replace(src, dst)
    lines = [_MULTISPACE.sub(" ", line).strip() for line in text.splitlines()]
    return "\n".join(lines)


# --------------------------------------------------------------------------- doubled glyphs


def _is_doubled_token(token: str, min_len: int = 4) -> bool:
    if len(token) < min_len or len(token) % 2:
        return False
    return all(token[i] == token[i + 1] for i in range(0, len(token), 2))


def looks_character_doubled(text: str, threshold: float = 0.6, min_tokens: int = 5) -> bool:
    """True when most alphabetic tokens read like ``"RReevveennuuee"``.

    Some PDF text layers carry every glyph twice. We test tokens of length ≥ 4 and
    report doubling when at least ``threshold`` of them are perfect pairs.
    """
    tokens = [t for t in re.split(r"\s+", text) if len(t) >= 4 and any(c.isalpha() for c in t)]
    if len(tokens) < min_tokens:
        return False
    doubled = sum(1 for t in tokens if _is_doubled_token(t))
    return doubled / len(tokens) >= threshold


def _greedy_collapse(token: str) -> str:
    out: list[str] = []
    i = 0
    while i < len(token):
        out.append(token[i])
        i += 2 if i + 1 < len(token) and token[i + 1] == token[i] else 1
    return "".join(out)


def collapse_doubled_characters(text: str, max_ratio: float = 0.6) -> str:
    """Collapse ``"RReevveennuuee 11,,000000"`` → ``"Revenue 1,000"`` token by token.

    Each token is collapsed greedily (``"mmiillliioonn"`` → ``"million"`` even when an
    upstream de-duplication already dropped a glyph) but only when the collapse shrinks
    it to at most ``max_ratio`` of its length — a healthy token such as ``"balloon"`` or
    ``"1,000"`` shrinks far less and is left alone. Callers only invoke this on pages
    that :func:`looks_character_doubled`.
    """

    def fix(token: str) -> str:
        if len(token) < 2:
            return token
        collapsed = _greedy_collapse(token)
        return collapsed if len(collapsed) / len(token) <= max_ratio else token

    return "\n".join(" ".join(fix(t) for t in line.split(" ")) for line in text.splitlines())


def repair_doubled_page(page: PageText) -> PageText:
    """Return ``page`` with doubled glyphs collapsed in its text and table cells, if detected."""
    if not looks_character_doubled(page.text):
        return page
    tables = [[[collapse_doubled_characters(c) for c in row] for row in t] for t in page.tables]
    return PageText(page=page.page, text=collapse_doubled_characters(page.text), tables=tables)


# --------------------------------------------------------------------------- line dedup


def dedupe_lines(lines: list[str], window: int = 3) -> list[str]:
    """Remove a line when an identical non-empty line occurred within the last ``window`` lines."""
    out: list[str] = []
    recent: list[str] = []
    for line in lines:
        key = line.strip()
        if key and key in recent:
            continue
        out.append(line)
        recent.append(key)
        if len(recent) > window:
            recent.pop(0)
    return out


# --------------------------------------------------------------------------- headers/footers


def _mask(line: str) -> str:
    return _DIGITS.sub("#", line.strip().lower())


def find_running_lines(
    pages_lines: list[list[str]],
    min_page_ratio: float = 0.5,
    min_pages: int = 4,
    zone_lines: int = 3,
    long_line_ratio: float = 0.15,
    long_line_chars: int = 25,
) -> set[str]:
    """Return masked lines that recur in the header/footer zone of many pages.

    Only the first and last ``zone_lines`` lines of each page are considered, so a
    frequent body line such as ``"Total"`` is never mistaken for a running header. Two
    thresholds apply: any line on ≥ ``min_page_ratio`` of pages, and long lines
    (≥ ``long_line_chars``) on ≥ ``long_line_ratio`` of pages — section navigation
    headers in designed reports change wording per section and only recur on a share
    of pages, but a long line repeating in the zone is never body content.
    """
    if len(pages_lines) < min_pages:
        return set()
    counter: Counter[str] = Counter()
    for lines in pages_lines:
        nonblank = [ln for ln in lines if ln.strip()]
        zone = nonblank[:zone_lines] + nonblank[-zone_lines:]
        for masked in {_mask(ln) for ln in zone if len(ln.strip()) >= 3}:
            counter[masked] += 1
    needed = max(2, int(round(min_page_ratio * len(pages_lines))))
    needed_long = max(3, int(round(long_line_ratio * len(pages_lines))))
    return {
        masked
        for masked, n in counter.items()
        if n >= needed or (len(masked) >= long_line_chars and n >= needed_long)
    }


def strip_running_lines(lines: list[str], running: set[str]) -> list[str]:
    """Drop lines whose masked form is in ``running``."""
    if not running:
        return lines
    return [ln for ln in lines if _mask(ln) not in running]


# --------------------------------------------------------------------------- orchestration


def clean_pages(pages: list[PageText], options: CleaningOptions | None = None) -> list[PageText]:
    """Apply the full cleaning pipeline to a document's pages.

    Returns new :class:`PageText` objects (tables untouched). Pages whose cleaned text is
    shorter than ``min_chunk_chars`` are dropped unless they contain tables.
    """
    opts = options or CleaningOptions()
    normalised: list[list[str]] = []
    pages = [
        repair_doubled_page(PageText(page=p.page, text=normalise_text(p.text), tables=p.tables))
        for p in pages
    ]
    for page in pages:
        lines = page.text.split("\n")
        if opts.dedupe_lines:
            lines = dedupe_lines(lines, opts.dedupe_window)
        normalised.append(lines)

    running = find_running_lines(
        normalised,
        min_page_ratio=opts.header_footer_min_page_ratio,
        min_pages=opts.min_pages_for_header_detection,
        zone_lines=opts.header_footer_zone_lines,
        long_line_ratio=opts.header_footer_long_line_ratio,
        long_line_chars=opts.header_footer_long_line_chars,
    )

    cleaned: list[PageText] = []
    for page, lines in zip(pages, normalised, strict=True):
        lines = strip_running_lines(lines, running)
        text = "\n".join(lines).strip()
        text = re.sub(r"\n{3,}", "\n\n", text)
        if len(text) < opts.min_chunk_chars and not page.tables:
            continue
        cleaned.append(PageText(page=page.page, text=text, tables=page.tables))
    return cleaned
