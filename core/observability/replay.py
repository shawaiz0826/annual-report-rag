"""Replay cache: deterministic answers for demos, keyed on model, prompts and conversation."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from core.agent.prompts import prompt_versions
from core.models import AgentAnswer, Session, Trace


class ReplayCache:
    """JSON file mapping a conversation key to a recorded (answer, trace)."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._data: dict[str, dict] = {}
        if path.exists():
            self._data = json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def key(model: str, session: Session, message: str, show_model_knowledge: bool) -> str:
        """sha256 over model, prompt versions and the ordered user messages of the session."""
        history = [t.content for t in session.turns if t.role == "user"]
        material = json.dumps(
            {
                "model": model,
                "prompts": prompt_versions(),
                "messages": [*history, message],
                "show_model_knowledge": show_model_knowledge,
            },
            sort_keys=True,
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]

    def get(self, key: str) -> tuple[AgentAnswer, Trace] | None:
        """Cached entry or None."""
        entry = self._data.get(key)
        if not entry:
            return None
        return AgentAnswer.model_validate(entry["answer"]), Trace.model_validate(entry["trace"])

    def put(self, key: str, answer: AgentAnswer, trace: Trace) -> None:
        """Store and persist."""
        self._data[key] = {
            "answer": answer.model_dump(mode="json"),
            "trace": trace.model_dump(mode="json"),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data, ensure_ascii=False), encoding="utf-8")

    def __len__(self) -> int:
        return len(self._data)
