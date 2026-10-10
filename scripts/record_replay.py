"""Record the demo script into the replay cache: `python scripts/record_replay.py [--model M]`.

Runs every session in scripts/demo_turns.json live, storing each answer under a key of
(model, prompt versions, conversation so far). `python -m apps.cli chat --replay` then
serves those answers with a [replay] marker when the same conversation is typed again.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rich.console import Console  # noqa: E402

from core.service import ChatService  # noqa: E402

TURNS = Path(__file__).resolve().parent / "demo_turns.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Record demo answers into the replay cache")
    parser.add_argument("--model", default=None, help="model id (default: configured primary)")
    parser.add_argument("--show-model-knowledge", action="store_true")
    args = parser.parse_args()

    console = Console()
    service = ChatService()
    service.record_enabled = True
    sessions = json.loads(TURNS.read_text(encoding="utf-8"))["sessions"]
    failures = 0
    for turns in sessions:
        session = service.new_session()
        for turn in turns:
            answer, trace = service.chat(
                session.session_id,
                turn,
                model=args.model,
                show_model_knowledge=args.show_model_knowledge,
            )
            status = (
                "[green]ok[/green]"
                if trace.error is None
                else f"[red]error: {trace.error[:80]}[/red]"
            )
            failures += int(trace.error is not None)
            console.print(
                f"{status}  {turn}  → answered={answer.answered}, {len(answer.citations)} citations, {trace.latency_ms / 1000:.0f}s"
            )
    console.print(
        f"replay cache now holds {len(service.replay)} entries → {service.settings.paths.replay_cache}"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
