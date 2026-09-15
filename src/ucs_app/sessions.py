"""Browser-session state and structured UCS activity events."""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Mapping, Optional, Set
from uuid import uuid4

from ucs_contracts.arrangements import TargetArrangement

ActivityKind = Literal[
    "input",
    "session_state",
    "proposal",
    "validation",
    "agent_action",
    "clarification",
    "error",
    "cancellation",
    "command_publication",
    "progress",
    "result",
]


@dataclass(frozen=True)
class ActivityEvent:
    """One browser-safe event in the structured activity trail."""

    sequence: int
    kind: ActivityKind
    message: str
    details: Mapping[str, object]

    def as_dict(self) -> Dict[str, object]:
        """Return the JSON-safe representation delivered through SSE."""

        return {
            "sequence": self.sequence,
            "kind": self.kind,
            "message": self.message,
            "details": dict(self.details),
        }

    def as_sse(self) -> str:
        """Encode the event as one Server-Sent Events data frame."""

        return f"id: {self.sequence}\ndata: {json.dumps(self.as_dict())}\n\n"


@dataclass
class ActiveCommand:
    """The command currently owned by one browser session."""

    message_id: str
    target: TargetArrangement


@dataclass
class BrowserSession:
    """UCS state belonging to one browser session."""

    session_id: str
    workflow_id: Optional[str] = None
    original_request: str = ""
    _sequence: int = 0
    active_command: Optional[ActiveCommand] = None
    last_successful_arrangement: Optional[TargetArrangement] = None
    request_in_progress: bool = False
    awaiting_reply: bool = False
    clarification_turn: int = 0
    agent_steps: int = 0
    seen_request_ids: Set[str] = field(default_factory=set)
    _events: List[ActivityEvent] = field(default_factory=list)
    _subscribers: Set[asyncio.Queue[ActivityEvent]] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def begin_request(self, text: str) -> None:
        """Keep browser history scoped to the latest request without reusing SSE IDs."""

        self.original_request = text
        self._events.clear()

    def public_state(self) -> Dict[str, object]:
        """Authoritative browser guards, separate from transient page state."""

        return {
            "original_request": self.original_request,
            "processing": self.request_in_progress,
            "active": self.active_command is not None,
            "outcome_unknown": self.active_command is not None and not self.request_in_progress,
            "awaiting_reply": self.awaiting_reply,
            "workflow_id": self.workflow_id,
            "turn": self.clarification_turn,
        }

    def snapshot(self) -> Dict[str, object]:
        """Restore current request events and guards on every SSE connection."""

        return {**self.public_state(), "events": [event.as_dict() for event in self._events]}

    def publish_state(self) -> None:
        self.emit(kind="session_state", message="", details=self.public_state())

    def emit(
        self,
        *,
        kind: ActivityKind,
        message: str,
        details: Optional[Mapping[str, object]] = None,
    ) -> ActivityEvent:
        """Append and broadcast one browser-safe activity event."""

        self._sequence += 1
        event = ActivityEvent(
            sequence=self._sequence,
            kind=kind,
            message=message,
            details={} if details is None else dict(details),
        )
        self._events.append(event)
        for subscriber in self._subscribers:
            subscriber.put_nowait(event)
        return event

    def subscribe(self) -> asyncio.Queue[ActivityEvent]:
        """Attach one SSE stream to this browser session."""

        subscriber: asyncio.Queue[ActivityEvent] = asyncio.Queue()
        self._subscribers.add(subscriber)
        return subscriber

    def unsubscribe(self, subscriber: asyncio.Queue[ActivityEvent]) -> None:
        """Detach a closed SSE stream."""

        self._subscribers.discard(subscriber)

    def activate(self, message_id: str, target: TargetArrangement) -> None:
        """Activate the exact validated target for automatic delivery."""

        if self.active_command is not None:
            raise RuntimeError("browser session already has an Active command")
        self.active_command = ActiveCommand(message_id=message_id, target=target)

    def finish_active(self, *, successful: bool) -> Optional[TargetArrangement]:
        """Finish the Active command and retain its arrangement on success."""

        if self.active_command is None:
            raise RuntimeError("browser session has no Active command")
        if successful:
            self.last_successful_arrangement = self.active_command.target
        self.active_command = None
        return self.last_successful_arrangement


class SessionStore:
    """Own the lifetime and lookup of browser sessions."""

    def __init__(self) -> None:
        self._sessions: Dict[str, BrowserSession] = {}

    def create(self) -> BrowserSession:
        """Create a new isolated browser session."""

        session_id = str(uuid4())
        session = BrowserSession(session_id=session_id)
        self._sessions[session_id] = session
        return session

    def get(self, session_id: Optional[str]) -> Optional[BrowserSession]:
        """Return an existing session without silently creating one."""

        if session_id is None:
            return None
        return self._sessions.get(session_id)
