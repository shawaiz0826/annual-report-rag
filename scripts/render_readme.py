"""Fill README sections from recorded artefacts: `python scripts/render_readme.py`.

* "A short transcript"  ← answers in the replay cache for the demo sessions
* "Evaluation"          ← eval/reports/latest.md (comparison) or the newest single report

Sections are delimited by HTML comment markers in README.md so re-running is idempotent.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.agent.prompts import prompt_versions  # noqa: E402
from core.models import AgentAnswer, Session, Trace, Turn  # noqa: E402
from core.observability.replay import ReplayCache  # noqa: E402
from core.service import ChatService  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
README = ROOT / "README.md"
TURNS = ROOT / "scripts" / "demo_turns.json"
REPORTS = ROOT / "eval" / "reports"


def _replace_section(text: str, name: str, body: str) -> str:
    start, end = f"<!-- {name}:start -->", f"<!-- {name}:end -->"
    block = f"{start}\n{body.strip()}\n{end}"
    if start in text and end in text:
        return re.sub(
            re.escape(start) + r".*?" + re.escape(end), lambda _m: block, text, flags=re.S
        )
    raise SystemExit(f"README.md lacks the {name} markers")


def transcript(service: ChatService, max_sessions: int = 6) -> str:
    cache: ReplayCache = service.replay
    model = service.settings.llm_model
    sessions = json.loads(TURNS.read_text(encoding="utf-8"))["sessions"]
    out: list[str] = []
    shown = 0
    for turns in sessions:
        history: list[Turn] = []
        rendered: list[str] = []
        for turn in turns:
            fake = Session(session_id="demo", created_at="", turns=history)
            hit = cache.get(cache.key(model, fake, turn, False))
            history.append(Turn(role="user", content=turn))
            if hit is None:
                rendered = []
                break
            answer, trace = hit
            rendered.append(_render_turn(turn, answer, trace))
        if rendered:
            out.append("\n\n".join(rendered))
            shown += 1
        if shown >= max_sessions:
            break
    if not out:
        return "_No recorded answers yet — run `make record-demo`._"
    header = f"Recorded with `{model}` (prompt versions {prompt_versions()}), served verbatim by `python -m apps.cli chat --replay`."
    return header + "\n\n" + "\n\n---\n\n".join(out)


def _render_turn(question: str, answer: AgentAnswer, trace: Trace) -> str:
    cites = ", ".join(dict.fromkeys(c.label() for c in answer.citations)) or "—"
    caveats = ("\n> ⚠️ " + "\n> ⚠️ ".join(answer.caveats)) if answer.caveats else ""
    u = trace.usage
    footer = f"`{u.prompt_tokens}/{u.completion_tokens} tokens · {trace.latency_ms / 1000:.0f}s · confidence {answer.confidence} · tools: {', '.join(t.tool for t in trace.tool_calls)}`"
    body = answer.answer_markdown.strip().replace("\n", "\n> ")
    return f"**you ›** {question}\n\n> {body}\n>\n> **Sources:** {cites}{caveats}\n\n{footer}"


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
    service = ChatService()
    text = README.read_text(encoding="utf-8")
    text = _replace_section(text, "transcript", transcript(service))
    text = _replace_section(text, "evaluation", evaluation())
    README.write_text(text, encoding="utf-8")
    print("README updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
