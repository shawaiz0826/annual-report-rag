"""Fill the README "Evaluation" section from eval reports: `python scripts/render_readme.py`.

Reads eval/reports/latest.md (the two-model comparison) or, failing that, the newest single
report. The section is delimited by HTML comment markers in README.md so re-running is idempotent.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
REPORTS = ROOT / "eval" / "reports"


def _replace_section(text: str, name: str, body: str) -> str:
    start, end = f"<!-- {name}:start -->", f"<!-- {name}:end -->"
    block = f"{start}\n{body.strip()}\n{end}"
    if start in text and end in text:
        return re.sub(
            re.escape(start) + r".*?" + re.escape(end), lambda _m: block, text, flags=re.S
        )
    raise SystemExit(f"README.md lacks the {name} markers")


def evaluation() -> str:
    latest = REPORTS / "latest.md"
    if latest.exists():
        text = latest.read_text(encoding="utf-8")
        table = text.split("## Per case")[0].split("\n", 2)[-1].strip()
        return (
            table
            + "\n\nPer-case results and failures: [eval/reports/latest.md](eval/reports/latest.md)."
        )
    reports = sorted(REPORTS.glob("report_*.md"))
    if not reports:
        return "_No eval report yet — run `make eval`._"
    newest = reports[-1]
    text = newest.read_text(encoding="utf-8")
    head = text.split("## By category")[0].strip()
    head = "\n".join(head.splitlines()[1:]).strip()  # drop the H1
    return head + f"\n\nFull report: [eval/reports/{newest.name}](eval/reports/{newest.name})."


def main() -> int:
    text = README.read_text(encoding="utf-8")
    README.write_text(_replace_section(text, "evaluation", evaluation()), encoding="utf-8")
    print("README updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
