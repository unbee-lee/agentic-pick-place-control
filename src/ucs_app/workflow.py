"""Bounded LangGraph role routing and automatic validated delivery."""

import asyncio
from copy import deepcopy
from collections.abc import Mapping
from typing import Dict, List, Literal, Optional, TypedDict, cast
from uuid import uuid4

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from ucs_app.actions import Arrange, AskUser, Clarify, robot_action, command_action
from ucs_app.interfaces import AdapterFailure, AgentContext, RobotCommandAgent, RobotExecutionStation, UserCommandAgent
from ucs_app.sessions import BrowserSession, SessionStore
from ucs_app.transport import utc_timestamp
from ucs_contracts import ContractValidationError, validate_message, validate_target_positions


class WorkflowConflict(Exception):
    """A duplicate, stale, or ineligible browser action."""


class WorkflowFailure(Exception):
    """A safe public failure; no raw adapter exception is exposed."""


class WorkflowState(TypedDict, total=False):
    session_id: str
    workflow_id: str
    original_request: str
    replies: List[str]
    feedback: str
    candidate: Dict[str, object]
    assignment: Dict[str, object]
    command_metadata: Dict[str, object]
    repair_attempts: int
    assignment_revision: int
    question: str
    route: Literal["arrange", "clarify", "validate", "feedback", "repair", "deliver"]


class UcsWorkflow:
    def __init__(
        self, *, user_command_agent: UserCommandAgent,
        robot_command_agent: RobotCommandAgent,
        robot_execution_station: RobotExecutionStation, sessions: SessionStore,
        max_agent_steps: int = 12, max_clarifications: int = 3, max_repairs: int = 2,
        agent_timeout: float = 20, execution_timeout: float = 30,
    ) -> None:
        if min(max_agent_steps, max_clarifications, agent_timeout, execution_timeout) <= 0:
            raise ValueError("workflow limits must be positive")
        if max_repairs < 0:
            raise ValueError("max_repairs cannot be negative")
        self._max_repairs = max_repairs
        self._command_agent = user_command_agent
        self._robot_command_agent = robot_command_agent
        self._res = robot_execution_station
        self._sessions = sessions
        self._max_steps = max_agent_steps
        self._max_clarifications = max_clarifications
        self._agent_timeout = agent_timeout
        self._execution_timeout = execution_timeout
        graph = StateGraph(WorkflowState)
        graph.add_node("user_command_agent", self._user_command)
        graph.add_node("robot_command_agent", self._robot_command)
        graph.add_node("validation_gate", self._validate)
        graph.add_node("await_user", self._await_user)
        graph.add_node("deliver_target", self._deliver)
        graph.add_edge(START, "user_command_agent")
        graph.add_conditional_edges("user_command_agent", self._route,
                                    {"arrange": "robot_command_agent", "clarify": "await_user"})
        graph.add_conditional_edges("robot_command_agent", self._route,
                                    {"validate": "validation_gate", "feedback": "user_command_agent"})
        graph.add_conditional_edges("validation_gate", self._route,
                                    {"deliver": "deliver_target", "repair": "robot_command_agent"})
        graph.add_edge("await_user", END)
        graph.add_edge("deliver_target", END)
        self._graph = graph.compile(checkpointer=InMemorySaver())

    async def submit(self, session: BrowserSession, text: str, request_id: str) -> None:
        async with session.lock:
            if session.request_in_progress or session.active_command or session.awaiting_reply:
                raise WorkflowConflict("Finish the current request first")
            if self._sessions.request_seen(request_id):
                raise WorkflowConflict("This request was already submitted; it will not be sent again")
            if len(session.seen_request_ids) >= 256:
                raise WorkflowConflict("Session request limit reached; start a new browser session")
            session.begin_request(text)
            session.seen_request_ids.add(request_id)
            session.workflow_id = request_id
            session.agent_steps = 0
            session.clarification_turn = 0
            session.request_in_progress = True
        state: WorkflowState = {
            "session_id": session.session_id, "workflow_id": request_id,
            "original_request": text, "replies": [], "feedback": "",
            "candidate": {}, "assignment": {}, "command_metadata": {}, "repair_attempts": 0,
            "assignment_revision": 0, "question": "",
        }
        session.emit(kind="input", message="Arrangement request submitted",
                     details={"new_request": True, "arrangement_request": text})
        await self._run(session, state)

    async def answer(self, session: BrowserSession, workflow_id: str, turn: int, text: str) -> None:
        async with session.lock:
            self._require_reply(session, workflow_id, turn)
            previous = deepcopy(session.workflow_state)
            replies = list(previous["replies"])
            replies.append(text)
            session.awaiting_reply = False
            session.request_in_progress = True
        state = cast(WorkflowState, {
            **previous,
            "session_id": session.session_id, "workflow_id": workflow_id,
            "replies": replies, "feedback": "", "candidate": {}, "assignment": {}, "command_metadata": {}, "repair_attempts": 0, "question": "",
        })
        session.emit(kind="input", message="Clarification answer received",
                     details={"clarification_answers": replies})
        await self._run(session, state)

    async def cancel(self, session: BrowserSession, workflow_id: str, turn: int) -> None:
        async with session.lock:
            self._require_reply(session, workflow_id, turn)
            session.awaiting_reply = False
            session.workflow_id = None
            session.workflow_state = {}
            session.emit(kind="cancellation", message="Request cancelled; nothing sent")
            session.publish_state()

    def _require_reply(self, session: BrowserSession, workflow_id: str, turn: int) -> None:
        if (session.request_in_progress or session.active_command or not session.awaiting_reply
                or session.workflow_id != workflow_id or session.clarification_turn != turn):
            raise WorkflowConflict("This clarification is no longer current")

    async def _run(self, session: BrowserSession, state: WorkflowState) -> None:
        try:
            await self._graph.ainvoke(
                state,
                config=self._config(session.session_id, state["workflow_id"]),
            )
        except (Exception, asyncio.CancelledError) as error:
            session.awaiting_reply = False
            if session.workflow_id is None and any(event.kind == "result" for event in session._events):
                message = "Execution finished, but final workflow bookkeeping failed. Check the recorded result before starting another request."
            elif session.active_command is None:
                session.workflow_id = None
                message = "Request stopped because an adapter failed or a workflow limit was reached. Nothing was sent."
            else:
                message = "Execution outcome is unknown. The command will not be resent automatically."
            if isinstance(error, AdapterFailure):
                message += " " + error.public_hint
            elif isinstance(error, asyncio.TimeoutError) and session.active_command is not None:
                message += " Check the execution service and its configured timeout; establish the outcome before retrying."
            session.emit(kind="error", message=message,
                         details={"outcome_unknown": session.active_command is not None})
            if isinstance(error, asyncio.CancelledError):
                raise
            raise WorkflowFailure(message) from error
        finally:
            async with session.lock:
                session.request_in_progress = False
                session.publish_state()

    def _config(self, session_id: str, workflow_id: str) -> RunnableConfig:
        return {"configurable": {"thread_id": f"{session_id}:{workflow_id}"}, "recursion_limit": 32}

    def _session(self, state: WorkflowState) -> BrowserSession:
        session = self._sessions.get(state["session_id"])
        if session is None or session.workflow_id != state["workflow_id"]:
            raise WorkflowConflict("Workflow is no longer current")
        return session

    def _context(self, state: WorkflowState) -> AgentContext:
        return AgentContext(state["original_request"], tuple(state["replies"]),
                            state.get("feedback", ""), deepcopy(state.get("candidate", {})),
                            deepcopy(state.get("assignment", {})),
                            deepcopy(state.get("command_metadata", {})))

    def _step(self, session: BrowserSession) -> None:
        if session.agent_steps >= self._max_steps:
            raise RuntimeError("agent step limit reached")
        session.agent_steps += 1
        session.save()

    def _route(self, state: WorkflowState) -> str:
        return state["route"]

    async def _user_command(self, state: WorkflowState) -> Dict[str, object]:
        session = self._session(state)
        self._step(session)
        payload = await asyncio.wait_for(self._command_agent.decide(self._context(state)), self._agent_timeout)
        action = command_action.validate_python(dict(payload))
        session.emit(kind="agent_action", message=f"User Command Agent selected {action.action}")
        if isinstance(action, Clarify):
            return {"route": "clarify", "question": action.question}
        if state.get("feedback"):
            raise RuntimeError("intent clarification requires a real user reply")
        assert isinstance(action, Arrange)
        return {"route": "arrange", "assignment": action.assignment.model_dump(mode="json"),
                "assignment_revision": state.get("assignment_revision", 0) + 1,
                "repair_attempts": 0, "candidate": {}, "feedback": "",
                "command_metadata": {"schema_version": "1.0", "message_id": str(uuid4()),
                                     "type": "ARRANGE", "created_at": utc_timestamp()}}

    async def _robot_command(self, state: WorkflowState) -> Dict[str, object]:
        session = self._session(state)
        session.workflow_state = deepcopy(dict(state))
        self._step(session)
        payload = await asyncio.wait_for(self._robot_command_agent.decide(self._context(state)), self._agent_timeout)
        action = robot_action.validate_python(dict(payload))
        session.emit(kind="agent_action", message=f"Robot Command Agent selected {action.action}")
        if isinstance(action, AskUser):
            return {"route": "feedback", "feedback": action.question}
        return {"route": "validate", "candidate": deepcopy(action.command)}

    async def _validate(self, state: WorkflowState) -> Dict[str, object]:
        session = self._session(state)
        candidate = state["candidate"]
        errors: List[str] = []
        try:
            validate_message("command", candidate)
        except ContractValidationError:
            errors.append("Schema error: use the RES command schema, required fields, types and allowed values; omit unknown fields.")
        raw_target = candidate.get("target_positions")
        if not isinstance(raw_target, Mapping):
            errors.append("Schema error: target_positions must be an object.")
        else:
            try:
                validate_target_positions(cast(Mapping[str, object], raw_target))
            except ContractValidationError:
                errors.append("Arrangement error: assign E, B and H once each to distinct allowed front positions.")
            if raw_target != state["assignment"]:
                errors.append("Intent mismatch: target_positions must exactly match the interpreted assignment; do not choose different positions.")
        for field, value in state["command_metadata"].items():
            if candidate.get(field) != value:
                errors.append(f"Metadata error: preserve the application-provided {field}.")
        if errors:
            session.emit(kind="validation", message="Invalid command; returning to Robot Command Agent")
            if state["repair_attempts"] >= self._max_repairs:
                raise RuntimeError("command repair budget exhausted")
            return {"route": "repair", "feedback": " ".join(errors),
                    "repair_attempts": state["repair_attempts"] + 1}
        session.emit(kind="validation", message="Command schema, arrangement and intent consistency validated")
        session.emit(kind="proposal", message="Validated Target arrangement",
                     details={"target_positions": deepcopy(raw_target)})
        return {"route": "deliver", "candidate": deepcopy(candidate), "feedback": ""}

    async def _await_user(self, state: WorkflowState) -> Dict[str, object]:
        session = self._session(state)
        if session.clarification_turn >= self._max_clarifications:
            raise RuntimeError("clarification limit reached")
        session.clarification_turn += 1
        session.awaiting_reply = True
        session.workflow_state = deepcopy(dict(state))
        session.emit(kind="clarification", message=state["question"], details={
            "workflow_id": session.workflow_id, "turn": session.clarification_turn,
        })
        return {}

    async def _deliver(self, state: WorkflowState) -> Dict[str, object]:
        session = self._session(state)
        command = deepcopy(state["candidate"])
        raw_target = command["target_positions"]
        if not isinstance(raw_target, Mapping) or raw_target != state["assignment"]:
            raise RuntimeError("validated target changed before dispatch")
        validate_target_positions(cast(Mapping[str, object], raw_target))
        validate_message("command", command)
        session.workflow_state = deepcopy(dict(state))
        self._sessions.reserve(session, command)
        session.emit(kind="command_publication", message="Sending validated target automatically",
                     details={"message_id": command["message_id"]})
        result = await asyncio.wait_for(self._execute(session, command), self._execution_timeout)
        self._sessions.complete(session, dict(result))
        return {}

    async def _execute(self, session: BrowserSession, command: Mapping[str, object]) -> Mapping[str, object]:
        terminal: Optional[Mapping[str, object]] = None
        async for update in self._res.execute(command):
            validate_message(update.message_type, update.payload)
            if update.payload.get("message_id") != command["message_id"] or terminal is not None:
                raise RuntimeError("uncorrelated or post-terminal RES update")
            if update.message_type == "result":
                terminal = update.payload
            else:
                session.emit(kind="progress", message="RES is busy", details={
                    "message_id": command["message_id"], "stage": update.payload.get("stage"),
                })
        if terminal is None:
            raise RuntimeError("RES returned no terminal result")
        return terminal
