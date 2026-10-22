"""Native HTTP boundary checks; no model service is required."""

import asyncio
import json
from typing import Mapping, AsyncIterator

import httpx
import pytest

from ucs_app.controlled import ControlledRobotExecutionStation
from ucs_app.interfaces import AgentContext, ControllerUpdate
from ucs_app.ollama import OllamaClient, OllamaConfig, OllamaError, OllamaUserCommandAgent, OllamaRobotCommandAgent
from ucs_app.sessions import SessionStore
from ucs_app.workflow import UcsWorkflow, WorkflowFailure

TARGET = {"E": "front_left", "B": "front_center", "H": "front_right"}


def response(name: str, arguments: object) -> dict[str, object]:
    return {"done": True, "message": {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": name, "arguments": arguments}}
    ]}}


@pytest.mark.parametrize("body", [
    {}, {"done": False}, {"done": True, "message": None},
    response("execute", {}), response("validate_command", {"command": {}}),
    response("handoff_assignment", {"assignment": {"E": "front_left"}}),
    response("ask_clarification", {"question": "?", "action": "CLARIFY"}),
    response("ask_clarification", '{"question":"?"}'),
    {**response("ask_clarification", {"question": "?"}), "done_reason": "length"},
    {"done": True, "message": {"role": "assistant", "content": "PRIVATE RAW OUTPUT"}},
])
def test_malformed_actions_are_bounded(body: object) -> None:
    async def run() -> None:
        client = OllamaClient(OllamaConfig(), transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json=body)))
        with pytest.raises(OllamaError, match="^Ollama returned an invalid role action$"):
            await client.decide("user", AgentContext("private request"))
    asyncio.run(run())


@pytest.mark.parametrize("status,content", [(503, b"PRIVATE"), (200, b"invalid PRIVATE"), (200, b"x" * 1_048_577)])
def test_http_failures_hide_response(status: int, content: bytes) -> None:
    async def run() -> None:
        client = OllamaClient(OllamaConfig(), transport=httpx.MockTransport(
            lambda request: httpx.Response(status, content=content)))
        with pytest.raises(OllamaError) as error:
            await client.decide("user", AgentContext("private"))
        assert "PRIVATE" not in str(error.value)
    asyncio.run(run())


class RecordingRES(ControlledRobotExecutionStation):
    def __init__(self) -> None:
        super().__init__(result_delay_seconds=0)
        self.commands: list[Mapping[str, object]] = []

    async def execute(self, command: Mapping[str, object]) -> AsyncIterator[ControllerUpdate]:
        self.commands.append(command)
        async for update in super().execute(command):
            yield update


@pytest.mark.parametrize("always_invalid", [False, True])
def test_native_workflow_repairs_exact_command_or_exhausts(always_invalid: bool) -> None:
    async def run() -> None:
        evidence: list[dict[str, object]] = []
        candidates: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            assert payload["model"] == "test-model" and payload["stream"] is False
            assert "format" not in payload
            context = json.loads(payload["messages"][-1]["content"])
            evidence.append(context)
            if len(evidence) == 1:
                return httpx.Response(200, json=response("handoff_assignment", {"assignment": TARGET}))
            command = {**context["command_metadata"], "target_positions": context["assignment"]}
            if always_invalid or len(candidates) == 0:
                command["target_positions"] = {**TARGET, "B": "front_right", "H": "front_center"}
            candidates.append(command)
            return httpx.Response(200, json=response("validate_command", {"command": command}))

        client = OllamaClient(OllamaConfig(model="test-model"), transport=httpx.MockTransport(handler))
        store, res = SessionStore(), RecordingRES()
        workflow = UcsWorkflow(user_command_agent=OllamaUserCommandAgent(client),
                               robot_command_agent=OllamaRobotCommandAgent(client),
                               robot_execution_station=res, sessions=store)
        session = store.create()
        if always_invalid:
            with pytest.raises(WorkflowFailure):
                await workflow.submit(session, "original evidence", "id")
            assert len(candidates) == 3 and not res.commands
        else:
            await workflow.submit(session, "original evidence", "id")
            assert res.commands == [candidates[-1]]
        assert evidence[-1]["original_request"] == "original evidence"
        assert evidence[-1]["assignment"] == TARGET
        assert "Intent mismatch" in str(evidence[-1]["feedback"])
        assert evidence[-1]["candidate"] == candidates[-2]
    asyncio.run(run())


def test_clarification_and_sessions_have_only_explicit_evidence() -> None:
    async def run() -> None:
        seen: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            context = json.loads(json.loads(request.content)["messages"][-1]["content"])
            seen.append(context)
            return httpx.Response(200, json=response("ask_clarification", {"question": "Where should the bear go?"}))

        client = OllamaClient(OllamaConfig(), transport=httpx.MockTransport(handler))
        store, res = SessionStore(), RecordingRES()
        workflow = UcsWorkflow(user_command_agent=OllamaUserCommandAgent(client),
                               robot_command_agent=OllamaRobotCommandAgent(client),
                               robot_execution_station=res, sessions=store)
        first, second = store.create(), store.create()
        await workflow.submit(first, "first private request", "one")
        assert first.awaiting_reply and len(seen) == 1 and not res.commands
        await workflow.submit(second, "second private request", "two")
        await workflow.answer(first, "one", 1, "bear centre")
        assert seen[1]["original_request"] == "second private request" and seen[1]["replies"] == []
        assert seen[2]["original_request"] == "first private request" and seen[2]["replies"] == ["bear centre"]
        await workflow.cancel(first, "one", 2)
        assert not res.commands and first.workflow_id is None
    asyncio.run(run())


def test_serialization_timeout_cancellation_and_recovery() -> None:
    async def run() -> None:
        entered = asyncio.Event()
        release = asyncio.Event()
        requests: list[str] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            context = json.loads(json.loads(request.content)["messages"][-1]["content"])
            requests.append(context["original_request"])
            entered.set()
            await release.wait()
            return httpx.Response(200, json=response("ask_clarification", {"question": "Where?"}))

        client = OllamaClient(OllamaConfig(timeout_seconds=0.1), transport=httpx.MockTransport(handler))
        first = asyncio.create_task(client.decide("user", AgentContext("first")))
        await entered.wait()
        second = asyncio.create_task(client.decide("user", AgentContext("second")))
        await asyncio.sleep(0)
        assert requests == ["first"]
        second.cancel()
        with pytest.raises(asyncio.CancelledError):
            await second
        with pytest.raises(OllamaError, match="timed out"):
            await first
        release.set()
        await client.decide("user", AgentContext("recovered"))
        assert requests == ["first", "recovered"]
    asyncio.run(run())


def test_workflow_cancellation_is_observable_and_releases_session() -> None:
    async def run() -> None:
        entered = asyncio.Event()

        async def handler(request: httpx.Request) -> httpx.Response:
            entered.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        client = OllamaClient(OllamaConfig(), transport=httpx.MockTransport(handler))
        store, res = SessionStore(), RecordingRES()
        workflow = UcsWorkflow(user_command_agent=OllamaUserCommandAgent(client),
                               robot_command_agent=OllamaRobotCommandAgent(client),
                               robot_execution_station=res, sessions=store)
        session = store.create()
        task = asyncio.create_task(workflow.submit(session, "request", "one"))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not session.request_in_progress and session.workflow_id is None and not res.commands
        assert "Nothing was sent" in str(session.snapshot())
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["connection", "malformed"])
def test_browser_api_failure_has_no_raw_model_output(failure: str) -> None:
    from uuid import uuid4
    from ucs_app.app import create_app

    async def run() -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if failure == "connection":
                raise httpx.ConnectError("PRIVATE service diagnostic", request=request)
            return httpx.Response(200, json={"done": True, "message": {
                "role": "assistant", "content": "PRIVATE model reasoning"}})

        client = OllamaClient(OllamaConfig(), transport=httpx.MockTransport(handler))
        res = RecordingRES()
        app = create_app(user_command_agent=OllamaUserCommandAgent(client),
                         robot_command_agent=OllamaRobotCommandAgent(client),
                         robot_execution_station=res, composition_label="Test live adapters")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as browser:
            await browser.get("/")
            result = await browser.post("/arrangement-requests", json={"text": "request", "request_id": str(uuid4())})
            assert result.status_code == 503 and "Nothing was sent" in result.text
            assert "PRIVATE" not in result.text and not res.commands
    asyncio.run(run())


@pytest.mark.parametrize("kind", ["mixed", "multiple", "truncated", "bad-arguments"])
def test_rejected_clarification_never_uses_format_example(kind: str, caplog: pytest.LogCaptureFixture) -> None:
    """An invalid live response must not turn into the demonstration question."""
    async def run() -> None:
        calls = 0
        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            message: dict[str, object] = {"role": "assistant", "content": "", "tool_calls": [
                {"function": {"name": "ask_clarification", "arguments": {"question": "PRIVATE QUESTION"}}}
            ]}
            body: dict[str, object] = {"done": True, "message": message}
            if kind == "mixed":
                message["content"] = "PRIVATE REASONING"
            elif kind == "multiple":
                message["tool_calls"] = [
                    {"function": {"name": "ask_clarification", "arguments": {"question": "PRIVATE QUESTION"}}},
                    {"function": {"name": "PRIVATE TOOL", "arguments": {"PRIVATE ARG": "PRIVATE VALUE"}}},
                ]
            elif kind == "truncated":
                body["done_reason"] = "length"
            else:
                message["tool_calls"] = [{"function": {"name": "ask_clarification", "arguments": {"question": ""}}}]
            return httpx.Response(200, json=body)
        client = OllamaClient(OllamaConfig(), transport=httpx.MockTransport(handler))
        store, res = SessionStore(), RecordingRES()
        runner = UcsWorkflow(user_command_agent=OllamaUserCommandAgent(client),
                             robot_command_agent=OllamaRobotCommandAgent(client),
                             robot_execution_station=res, sessions=store)
        session = store.create()
        with pytest.raises(WorkflowFailure):
            await runner.submit(session, "PRIVATE request", "one")
        assert calls == 1 and not res.commands and not session.awaiting_reply
        assert not any(event.kind == "clarification" for event in session._events)
        assert "PRIVATE" not in caplog.text
        assert '"tool_count"' in caplog.text
    asyncio.run(run())


def test_format_demonstration_is_fixed_and_current_context_is_last() -> None:
    from dataclasses import asdict
    from ucs_app.ollama import _messages
    first = AgentContext("elephant left", ("bear centre, hippo right",), candidate={}, assignment={}, command_metadata={})
    second = AgentContext("another session")
    messages = _messages("user", first)
    assert len(messages) == 4
    assert messages[1:3] == _messages("user", second)[1:3]
    assert json.loads(str(messages[-1]["content"])) == {**asdict(first), "replies": list(first.replies)}
    assert messages[2]["content"] == ""
    assert len(_messages("robot", first)) == 2


def test_valid_native_question_is_model_authored_and_reply_completes() -> None:
    async def run() -> None:
        contexts: list[dict[str, object]] = []
        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            context = json.loads(payload["messages"][-1]["content"])
            contexts.append(context)
            if len(contexts) == 1:
                return httpx.Response(200, json=response("ask_clarification", {"question": "Which slots should bear and hippo occupy?"}))
            if len(contexts) == 2:
                return httpx.Response(200, json=response("handoff_assignment", {"assignment": TARGET}))
            return httpx.Response(200, json=response("validate_command", {"command": {
                **context["command_metadata"], "target_positions": context["assignment"]}}))
        client = OllamaClient(OllamaConfig(), transport=httpx.MockTransport(handler))
        store, res = SessionStore(), RecordingRES()
        runner = UcsWorkflow(user_command_agent=OllamaUserCommandAgent(client),
                             robot_command_agent=OllamaRobotCommandAgent(client),
                             robot_execution_station=res, sessions=store)
        session = store.create()
        await runner.submit(session, "elephant left", "one")
        assert session.awaiting_reply and not res.commands
        questions = [event.message for event in session._events if event.kind == "clarification"]
        assert questions == ["Which slots should bear and hippo occupy?"]
        await runner.answer(session, "one", 1, "bear centre, hippo right")
        assert len(res.commands) == 1 and res.commands[0]["target_positions"] == TARGET
        assert contexts[1]["original_request"] == "elephant left"
        assert contexts[1]["replies"] == ["bear centre, hippo right"]
    asyncio.run(run())
