"""Browser-session state and structured UCS activity events."""

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Literal, Mapping, Optional, Set
from uuid import uuid4

from ucs_contracts.arrangements import TargetArrangement
from ucs_app.recovery import RecoveryJournal

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
    workflow_state: Dict[str, Any] = field(default_factory=dict)
    _persist: Optional[Callable[["BrowserSession"], None]] = field(default=None, repr=False)
    _events: List[ActivityEvent] = field(default_factory=list)
    _subscribers: Set[asyncio.Queue[ActivityEvent]] = field(default_factory=set)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    def begin_request(self, text: str) -> None:
        """Keep browser history scoped to the latest request without reusing SSE IDs."""

        self.original_request = text
        self.workflow_state = {}
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
        self.save()
        for subscriber in self._subscribers:
            subscriber.put_nowait(event)
        return event

    def save(self) -> None:
        if self._persist is not None:
            self._persist(self)

    def durable_state(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id, "workflow_id": self.workflow_id,
            "original_request": self.original_request, "sequence": self._sequence,
            "request_in_progress": self.request_in_progress, "awaiting_reply": self.awaiting_reply,
            "clarification_turn": self.clarification_turn, "agent_steps": self.agent_steps,
            "seen_request_ids": sorted(self.seen_request_ids), "workflow_state": self.workflow_state,
            "active_command": None if self.active_command is None else {
                "message_id": self.active_command.message_id,
                "target": self.active_command.target.model_dump(mode="json")},
            "last_successful_arrangement": None if self.last_successful_arrangement is None
                else self.last_successful_arrangement.model_dump(mode="json"),
            "events": [event.as_dict() for event in self._events],
        }

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

    def __init__(self, state_path: Optional[str] = None) -> None:
        self._sessions: Dict[str, BrowserSession] = {}
        self.listener_status = "disabled"
        self.journal = RecoveryJournal(state_path) if state_path is not None else None
        if self.journal is not None:
            for data in self.journal.sessions():
                session = BrowserSession(
                    session_id=data["session_id"], workflow_id=data["workflow_id"],
                    original_request=data["original_request"], _sequence=data["sequence"],
                    awaiting_reply=data["awaiting_reply"], clarification_turn=data["clarification_turn"],
                    agent_steps=data["agent_steps"], seen_request_ids=set(data["seen_request_ids"]),
                    workflow_state=data["workflow_state"], _persist=self.save,
                )
                session._events = [ActivityEvent(**event) for event in data["events"]]
                if data["last_successful_arrangement"] is not None:
                    session.last_successful_arrangement = TargetArrangement.model_validate(data["last_successful_arrangement"])
                if data["active_command"] is not None:
                    active = data["active_command"]
                    session.active_command = ActiveCommand(active["message_id"], TargetArrangement.model_validate(active["target"]))
                    session.awaiting_reply = False
                    session.emit(kind="error", message="Execution outcome is unknown after restart. The command will not be resent automatically.",
                                 details={"outcome_unknown": True})
                elif data["request_in_progress"] and session.workflow_id is not None and not session.awaiting_reply:
                    session.workflow_id = None
                    session.workflow_state = {}
                    session.emit(kind="error", message="Request interrupted by restart. Nothing was sent; submit a new request.")
                self._sessions[session.session_id] = session
                session.save()

    def save(self, session: BrowserSession) -> None:
        if self.journal is not None:
            self.journal.save(session.session_id, session.durable_state())

    def request_seen(self, request_id: str) -> bool:
        return any(request_id in session.seen_request_ids for session in self._sessions.values())

    def reserve(self, session: BrowserSession, command: Dict[str, object]) -> None:
        if session.active_command is not None:
            raise RuntimeError("browser session already has an Active command")
        target = TargetArrangement.model_validate(command["target_positions"])
        data = session.durable_state()
        data["active_command"] = {"message_id": command["message_id"], "target": target.model_dump(mode="json")}
        if self.journal is not None:
            self.journal.reserve(str(command["message_id"]), session.session_id, command, data)
        session.activate(str(command["message_id"]), target)

    def complete(self, session: BrowserSession, result: Dict[str, Any], *, reconciled: bool = False) -> None:
        """Commit a terminal snapshot and journal together before exposing completion."""
        active = session.active_command
        if active is None or active.message_id != result["message_id"]:
            raise RuntimeError("terminal result does not match active command")
        execution, verification = result["execution"]["status"], result["verification"]["status"]
        details = {"message_id": result["message_id"], "execution_status": execution,
                   "verification_status": verification}
        if reconciled:
            details["reconciled"] = True
        event = ActivityEvent(session._sequence + 1, "result",
                              "Late RES result reconciled." if reconciled else
                              f"Execution: {execution}. Verification: {verification}.", details)
        data = session.durable_state()
        data.update(active_command=None, workflow_id=None, awaiting_reply=False,
                    request_in_progress=False, sequence=event.sequence,
                    events=[*data["events"], event.as_dict()])
        if execution == "COMPLETED":
            data["last_successful_arrangement"] = active.target.model_dump(mode="json")
        if self.journal is not None:
            self.journal.settle(str(result["message_id"]), session.session_id, data, result)
        session.finish_active(successful=execution == "COMPLETED")
        session.workflow_id = None
        session.awaiting_reply = False
        session._sequence = event.sequence
        session._events.append(event)
        for subscriber in session._subscribers:
            subscriber.put_nowait(event)

    def reconcile(self) -> None:
        if self.journal is None:
            return
        for session in self._sessions.values():
            if session.active_command is None or session.request_in_progress:
                continue
            result = self.journal.result(session.active_command.message_id)
            if result is None:
                continue
            self.complete(session, result, reconciled=True)
            session.publish_state()

    def close(self) -> None:
        if self.journal is not None:
            self.journal.close()

    def create(self) -> BrowserSession:
        """Create a new isolated browser session."""

        session_id = str(uuid4())
        session = BrowserSession(session_id=session_id, _persist=self.save)
        self._sessions[session_id] = session
        session.save()
        return session

    def get(self, session_id: Optional[str]) -> Optional[BrowserSession]:
        """Return an existing session without silently creating one."""

        if session_id is None:
            return None
        return self._sessions.get(session_id)
