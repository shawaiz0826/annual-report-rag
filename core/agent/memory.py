"""Session stores: conversation turns plus slot memory for follow-up resolution."""

from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from core.models import AgentAnswer, Session, Turn


def new_session_id() -> str:
    """Short random id."""
    return uuid.uuid4().hex[:12]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class SessionStore(Protocol):
    """Persistence for :class:`Session`."""

    def create(self) -> Session:
        """Start a new session."""
        ...

    def get(self, session_id: str) -> Session | None:
        """Load a session or None."""
        ...

    def append(self, session_id: str, turn: Turn) -> None:
        """Add a turn."""
        ...

    def update_slots(self, session_id: str, slots: dict[str, Any]) -> None:
        """Replace the slot memory."""
        ...


def slots_from_answer(
    answer: AgentAnswer, previous: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Slot memory after an answer: last entities / years / metrics (fall back to previous)."""
    previous = previous or {}
    return {
        "entities": answer.entities_used or previous.get("entities", []),
        "years": answer.years_used or previous.get("years", []),
        "metrics": answer.metrics_used or previous.get("metrics", []),
        "resolved_question": answer.resolved_question,
    }


class InMemorySessionStore:
    """Dict-backed store for tests and the eval harness."""

    def __init__(self) -> None:
        self._sessions: dict[str, Session] = {}

    def create(self) -> Session:
        """Start a new session."""
        session = Session(session_id=new_session_id(), created_at=_now())
        self._sessions[session.session_id] = session
        return session

    def get(self, session_id: str) -> Session | None:
        """Load a session or None."""
        return self._sessions.get(session_id)

    def append(self, session_id: str, turn: Turn) -> None:
        """Add a turn."""
        self._sessions[session_id].turns.append(turn)

    def update_slots(self, session_id: str, slots: dict[str, Any]) -> None:
        """Replace the slot memory."""
        self._sessions[session_id].slots = slots


class SQLiteSessionStore:
    """Sessions as JSON blobs in SQLite."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path))
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS sessions (session_id TEXT PRIMARY KEY, created_at TEXT, json TEXT)"
        )

    def create(self) -> Session:
        """Start a new session."""
        session = Session(session_id=new_session_id(), created_at=_now())
        self._save(session)
        return session

    def get(self, session_id: str) -> Session | None:
        """Load a session or None."""
        row = self._conn.execute(
            "SELECT json FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        return Session.model_validate_json(row[0]) if row else None

    def append(self, session_id: str, turn: Turn) -> None:
        """Add a turn (creates the session if unknown)."""
        session = self.get(session_id) or Session(session_id=session_id, created_at=_now())
        session.turns.append(turn)
        self._save(session)

    def update_slots(self, session_id: str, slots: dict[str, Any]) -> None:
        """Replace the slot memory."""
        session = self.get(session_id) or Session(session_id=session_id, created_at=_now())
        session.slots = slots
        self._save(session)

    def _save(self, session: Session) -> None:
        with self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO sessions (session_id, created_at, json) VALUES (?, ?, ?)",
                (
                    session.session_id,
                    session.created_at,
                    json.dumps(session.model_dump(mode="json")),
                ),
            )


def build_session_store(backend: str, path: Path) -> SessionStore:
    """Factory keyed on ``sessions.backend``."""
    if backend == "memory":
        return InMemorySessionStore()
    return SQLiteSessionStore(path)
