"""Explicit agent-role and execution adapters for the UCS."""

from dataclasses import dataclass
from typing import AsyncIterator, Literal, Mapping, Optional, Protocol, Tuple


class AdapterFailure(Exception):
    """Adapter error with a fixed public hint, independent of raw exception text."""

    public_hint = ""


@dataclass(frozen=True)
class AgentContext:
    """Request evidence passed to a role, including validation feedback."""

    original_request: str
    replies: Tuple[str, ...] = ()
    feedback: str = ""
    candidate: Optional[Mapping[str, object]] = None
    assignment: Optional[Mapping[str, object]] = None
    command_metadata: Optional[Mapping[str, object]] = None


class UserCommandAgent(Protocol):
    async def decide(self, context: AgentContext) -> Mapping[str, object]:
        """Select ARRANGE with explicit assignment, or CLARIFY with question."""


class RobotCommandAgent(Protocol):
    async def decide(self, context: AgentContext) -> Mapping[str, object]:
        """Select VALIDATE with candidate command, or ASK_USER with question."""


@dataclass(frozen=True)
class ControllerUpdate:
    message_type: Literal["status", "result"]
    payload: Mapping[str, object]


class RobotExecutionStation(Protocol):
    def execute(self, command: Mapping[str, object]) -> AsyncIterator[ControllerUpdate]:
        """Yield validated status messages followed by one terminal result."""
