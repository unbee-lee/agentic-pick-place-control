"""Opt-in, single-process durable journal. Never use this journal to resend work."""

import fcntl
import json
from pathlib import Path
import sqlite3
from typing import Any, Optional
from uuid import uuid4


class RecoveryJournal:
    """SQLite commits precede external effects; an OS lock rejects a second owner."""

    def __init__(self, path: str) -> None:
        self._lock = Path(path + ".lock").open("a+")
        try:
            fcntl.flock(self._lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.db = sqlite3.connect(path)
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (id TEXT PRIMARY KEY, data TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS dispatches (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL, command TEXT NOT NULL,
                    request_id TEXT NOT NULL, revision INTEGER NOT NULL,
                    result TEXT, settled INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS identities (id TEXT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)
            with self.db:
                self.db.execute("INSERT OR IGNORE INTO settings VALUES ('client_id', ?)",
                                ("ucs-recovery-" + uuid4().hex,))
        except BaseException:
            if hasattr(self, "db"):
                self.db.close()
            self._lock.close()
            raise

    @property
    def client_id(self) -> str:
        return str(self.db.execute("SELECT value FROM settings WHERE key='client_id'").fetchone()[0])

    def sessions(self) -> list[dict[str, Any]]:
        return [json.loads(row[0]) for row in self.db.execute("SELECT data FROM sessions")]

    def save(self, session_id: str, data: dict[str, Any]) -> None:
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO sessions VALUES (?, ?)", (session_id, json.dumps(data)))

    def reserve(self, message_id: str, session_id: str, command: dict[str, object],
                data: dict[str, Any]) -> None:
        with self.db:
            self.db.execute("INSERT INTO dispatches (id, session_id, command, request_id, revision) VALUES (?, ?, ?, ?, ?)",
                            (message_id, session_id, json.dumps(command), data["workflow_id"],
                             data["workflow_state"]["assignment_revision"]))
            self.db.execute("INSERT OR REPLACE INTO sessions VALUES (?, ?)", (session_id, json.dumps(data)))

    def record_result(self, payload: dict[str, object]) -> None:
        with self.db:
            self.db.execute("UPDATE dispatches SET result=? WHERE id=? AND result IS NULL AND settled=0",
                            (json.dumps(payload), payload["message_id"]))

    def result(self, message_id: str) -> Optional[dict[str, Any]]:
        row = self.db.execute("SELECT result FROM dispatches WHERE id=? AND settled=0", (message_id,)).fetchone()
        if row is None or row[0] is None:
            return None
        value: dict[str, Any] = json.loads(row[0])
        return value

    def settle(self, message_id: str, session_id: str, data: dict[str, Any],
               result: dict[str, object]) -> None:
        with self.db:
            self.db.execute("UPDATE dispatches SET settled=1, result=? WHERE id=?", (json.dumps(result), message_id))
            self.db.execute("INSERT OR REPLACE INTO sessions VALUES (?, ?)", (session_id, json.dumps(data)))

    def identities(self) -> set[str]:
        return {row[0] for row in self.db.execute("SELECT id FROM identities")}

    def remember(self, identity: str) -> None:
        with self.db:
            self.db.execute("INSERT INTO identities VALUES (?)", (identity,))

    def close(self) -> None:
        self.db.close()
        self._lock.close()
