"""Browser -> scripted Ollama HTTP -> production adapters -> real MQTT -> RES."""

from dataclasses import dataclass, field
from contextlib import contextmanager, nullcontext
import json
from pathlib import Path
import threading
import time
from typing import Iterator, cast
from urllib.error import URLError
from urllib.request import urlopen

import aiomqtt
import asyncio
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from playwright.sync_api import expect, sync_playwright
import pytest
import uvicorn

from ucs_app.controlled import ControlledUserCommandAgent, ControlledRobotCommandAgent
from ucs_app.interfaces import AgentContext
from ucs_app.mqtt import MqttConfig, COMMAND_TOPIC
from test_mqtt import broker, broker_port, environment, free_port, process, simulator


@dataclass
class Evidence:
    calls: list[dict[str, object]] = field(default_factory=list)
    candidates: list[dict[str, object]] = field(default_factory=list)
    commands: list[dict[str, object]] = field(default_factory=list)


@contextmanager
def server(app: FastAPI) -> Iterator[str]:
    port = free_port()
    service = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    thread = threading.Thread(target=service.run, daemon=True)
    thread.start()
    try:
        for _ in range(200):
            if service.started:
                break
            if not thread.is_alive():
                pytest.fail("Scripted model server stopped")
            time.sleep(0.02)
        else:
            pytest.fail("Scripted model server did not start")
        yield f"http://127.0.0.1:{port}"
    finally:
        service.should_exit = True
        thread.join(timeout=5)
        assert not thread.is_alive()


@contextmanager
def wire_observer(port: int, evidence: Evidence) -> Iterator[None]:
    ready, stop = threading.Event(), threading.Event()
    errors: list[BaseException] = []
    async def observe() -> None:
        async with MqttConfig(port=port).client() as client:
            await client.subscribe(COMMAND_TOPIC, qos=1)
            ready.set()
            while not stop.is_set():
                try:
                    message = await asyncio.wait_for(client.messages.__anext__(), 0.1)
                    evidence.commands.append(json.loads(bytes(message.payload)))
                except asyncio.TimeoutError:
                    pass
    def run() -> None:
        try:
            asyncio.run(observe())
        except BaseException as error:
            errors.append(error)
            ready.set()
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        assert ready.wait(5) and not errors
        yield
    finally:
        stop.set()
        thread.join(timeout=5)
        assert not thread.is_alive() and not errors


@pytest.fixture
def integrated(request: pytest.FixtureRequest, broker_port: int, tmp_path: Path) -> Iterator[tuple[str, Evidence]]:
    mode = getattr(request, "param", "success")
    evidence = Evidence()
    fake = FastAPI()
    @fake.post("/api/chat")
    async def chat(request: Request) -> JSONResponse:
        body = await request.json()
        data = json.loads(body["messages"][-1]["content"])
        role = "user" if body["tools"][0]["function"]["name"] == "handoff_assignment" else "robot"
        evidence.calls.append({"role": role, "context": data, "model": body["model"]})
        if mode == "unavailable":
            return JSONResponse(status_code=503, content={"error": "PRIVATE model diagnostic"})
        if mode == "timeout":
            await asyncio.sleep(0.5)
        if mode == "malformed":
            return JSONResponse({"done": True, "message": {"role": "assistant", "content": "PRIVATE output"}})
        context = AgentContext(data["original_request"], tuple(data["replies"]), data["feedback"],
                               data["candidate"], data["assignment"], data["command_metadata"])
        action = dict(await (ControlledUserCommandAgent().decide(context) if role == "user"
                             else ControlledRobotCommandAgent().decide(context)))
        if role == "robot" and action["action"] == "VALIDATE":
            candidate = dict(cast(dict[str, object], action["command"]))
            if mode == "exhaust" or (mode == "repair" and not evidence.candidates):
                candidate["target_positions"] = {"E": "front_right", "B": "front_center", "H": "front_left"}
            if not evidence.candidates and mode == "schema-repair":
                candidate["unexpected"] = True
            if not evidence.candidates and mode == "metadata-repair":
                candidate["message_id"] = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
            evidence.candidates.append(candidate)
            action["command"] = candidate
        name = {"ARRANGE": "handoff_assignment", "CLARIFY": "ask_clarification",
                "VALIDATE": "validate_command", "ASK_USER": "needs_user_clarification"}[str(action.pop("action"))]
        return JSONResponse({"done": True, "message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": name, "arguments": action}}]}})

    res_context = nullcontext() if mode == "missing-res" else simulator(broker_port, tmp_path)
    with server(fake) as model_url, res_context, wire_observer(broker_port, evidence):
        app_port = free_port()
        env = {**environment(broker_port), "UCS_OLLAMA_URL": model_url, "UCS_OLLAMA_MODEL": "scripted-test-model",
               "UCS_OLLAMA_TIMEOUT": "0.1" if mode == "timeout" else "5"}
        if mode == "missing-broker":
            env["UCS_MQTT_PORT"] = str(free_port())
        import sys
        with process([sys.executable, "-m", "uvicorn", "ucs_app.live:create_integrated_app", "--factory",
                      "--host", "127.0.0.1", "--port", str(app_port)], tmp_path / "integrated.log", env) as app:
            url = f"http://127.0.0.1:{app_port}"
            for _ in range(200):
                assert app.poll() is None
                try:
                    with urlopen(url + "/health", timeout=0.1):
                        break
                except (OSError, URLError):
                    time.sleep(0.02)
            else:
                pytest.fail("Integrated app did not start")
            yield url, evidence


def test_integrated_all_arrangements_and_actual_reply(integrated: tuple[str, Evidence]) -> None:
    from itertools import permutations
    url, evidence = integrated
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url)
            expect(page.get_by_test_id("ucs-status")).to_have_text("Ready")
            for index, positions in enumerate(permutations(["left", "centre", "right"])):
                page.get_by_test_id("arrangement-request").fill(", ".join(
                    f"{a} {p}" for a, p in zip(["elephant", "bear", "hippo"], positions)))
                with page.expect_response("**/arrangement-requests") as response:
                    page.get_by_role("button", name="Submit request").click()
                assert response.value.status == 202
                expect(page.get_by_test_id("ucs-status")).to_have_text("Completed")
                assert len(evidence.commands) == index + 1
                assert evidence.commands[-1] == evidence.candidates[-1]
                assert evidence.commands[-1]["target_positions"] == dict(zip(
                    ["E", "B", "H"], ["front_" + p.replace("centre", "center") for p in positions]))
                expect(page.locator('[data-event-kind="progress"]')).to_have_count(1)
            page.get_by_test_id("arrangement-request").fill("elephant left")
            page.get_by_role("button", name="Submit request").click()
            expect(page.get_by_test_id("ucs-status")).to_have_text("Needs clarification")
            assert len(evidence.commands) == 6
            page.get_by_test_id("arrangement-request").fill("bear centre, hippo right")
            page.get_by_role("button", name="Send answer").click()
            expect(page.get_by_test_id("ucs-status")).to_have_text("Completed")
            assert len(evidence.commands) == 7 and evidence.commands[-1] == evidence.candidates[-1]
            assert all(call["model"] == "scripted-test-model" for call in evidence.calls)
            expect(page.get_by_role("button", name="Confirm", exact=True)).to_have_count(0)
            expect(page.get_by_test_id("status-description")).to_contain_text("Physical placement was not verified")
        finally:
            browser.close()


@pytest.mark.parametrize("integrated,expected", [("repair", "Completed"),
                                                 ("schema-repair", "Completed"),
                                                 ("metadata-repair", "Completed"), ("exhaust", "Failed"),
                                                 ("unavailable", "Failed"), ("malformed", "Failed"),
                                                 ("timeout", "Failed")], indirect=["integrated"])
def test_integrated_repair_and_model_failures(integrated: tuple[str, Evidence], expected: str) -> None:
    url, evidence = integrated
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url)
            expect(page.get_by_test_id("ucs-status")).to_have_text("Ready")
            page.get_by_test_id("arrangement-request").fill("elephant left, bear centre, hippo right")
            page.get_by_role("button", name="Submit request").click()
            expect(page.get_by_test_id("ucs-status")).to_have_text(expected)
            expect(page.locator('[data-event-kind="clarification"]')).to_have_count(0)
            assert "PRIVATE" not in page.locator("body").inner_text()
            if expected == "Completed":
                assert len(evidence.candidates) == 2 and evidence.commands == [evidence.candidates[-1]]
                contexts = [cast(dict[str, object], c["context"]) for c in evidence.calls if c["role"] == "robot"]
                assert contexts[0]["assignment"] == contexts[1]["assignment"]
                assert contexts[0]["command_metadata"] == contexts[1]["command_metadata"]
                assert contexts[1]["candidate"] == evidence.candidates[0]
                fault = ("Schema error" if "unexpected" in evidence.candidates[0]
                         else "Metadata error" if evidence.candidates[0]["message_id"] != evidence.candidates[-1]["message_id"]
                         else "Intent mismatch")
                assert fault in str(contexts[1]["feedback"])
                assert len([c for c in evidence.calls if c["role"] == "user"]) == 1
            else:
                assert not evidence.commands
                if evidence.candidates:
                    assert len(evidence.candidates) == 3
                else:
                    expect(page.locator('[data-event-kind="error"]')).to_contain_text("Check the Ollama service")
        finally:
            browser.close()


def test_integrated_smoke_script_with_scripted_model(integrated: tuple[str, Evidence]) -> None:
    import subprocess
    import sys
    url, evidence = integrated
    result = subprocess.run([sys.executable, "scripts/smoke_integrated.py", url], capture_output=True,
                            text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("PASS") == 8
    assert len(evidence.commands) == 7


@pytest.mark.parametrize("integrated", ["missing-res", "missing-broker"], indirect=True)
def test_integrated_dependency_failure_preserves_guard(integrated: tuple[str, Evidence]) -> None:
    url, evidence = integrated
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url)
            expect(page.get_by_test_id("ucs-status")).to_have_text("Ready")
            page.get_by_test_id("arrangement-request").fill("elephant left, bear centre, hippo right")
            page.get_by_role("button", name="Submit request").click()
            expect(page.get_by_test_id("ucs-status")).to_have_text("Outcome unknown")
            expect(page.locator('[data-event-kind="error"]')).to_contain_text("Check")
            expect(page.locator('[data-event-kind="error"]')).to_contain_text("outcome before retrying")
            count = len(evidence.commands)
            assert count <= 1
            page.reload()
            expect(page.get_by_test_id("ucs-status")).to_have_text("Outcome unknown")
            expect(page.get_by_role("button", name="Submit request")).to_be_disabled()
            assert len(evidence.commands) == count
        finally:
            browser.close()


def test_integrated_conflict_reply_correlation_and_stale_guards(integrated: tuple[str, Evidence]) -> None:
    """A real reply revises intent; replayed requests/replies never call a model or RES."""
    from uuid import uuid4

    url, evidence = integrated
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(url)
            expect(page.get_by_test_id("ucs-status")).to_have_text("Ready")
            request_id = str(uuid4())
            submission = {"text": "elephant left, bear left, hippo right", "request_id": request_id}
            response = page.request.post(url + "/arrangement-requests", data=submission)
            assert response.status == 202
            pending = response.json()
            expect(page.get_by_test_id("ucs-status")).to_have_text("Needs clarification")
            assert not evidence.commands
            # Invalid arrangement -> RCA feedback -> UCA question, without rewriting intent.
            assert [call["role"] for call in evidence.calls] == ["user", "robot", "robot", "user"]
            first_context = cast(dict[str, object], evidence.calls[1]["context"])
            feedback_context = cast(dict[str, object], evidence.calls[-1]["context"])
            assert feedback_context["assignment"] == first_context["assignment"]
            assert feedback_context["feedback"]
            reply = {"text": "bear centre", "workflow_id": pending["workflow_id"], "turn": pending["turn"]}
            page.reload()
            expect(page.get_by_test_id("ucs-status")).to_have_text("Needs clarification")
            response = page.request.post(url + "/clarifications", data=reply)
            assert response.status == 202
            expect(page.get_by_test_id("ucs-status")).to_have_text("Completed")
            assert evidence.commands == [evidence.candidates[-1]]
            revised = cast(dict[str, object], evidence.calls[-1]["context"])
            assert revised["assignment"] == {"E": "front_left", "B": "front_center", "H": "front_right"}
            original_metadata = cast(dict[str, object], first_context["command_metadata"])
            revised_metadata = cast(dict[str, object], revised["command_metadata"])
            assert revised_metadata["message_id"] != original_metadata["message_id"]
            assert revised["replies"] == ["bear centre"]
            snapshot = page.evaluate("""() => new Promise((resolve, reject) => {
                const source = new EventSource('/events');
                source.addEventListener('snapshot', event => {
                    source.close(); resolve(JSON.parse(event.data));
                });
                source.onerror = () => { source.close(); reject(new Error('snapshot failed')); };
            })""")
            events = snapshot["events"]
            for kind in ("command_publication", "progress", "result"):
                matched = [event for event in events if event["kind"] == kind]
                assert len(matched) == 1
                assert matched[0]["details"]["message_id"] == evidence.commands[0]["message_id"]
            result = next(event for event in events if event["kind"] == "result")
            assert result["details"]["execution_status"] == "COMPLETED"
            assert result["details"]["verification_status"] == "NOT_RUN"
            call_count = len(evidence.calls)
            for text in ("bear centre", "thanks", "yes"):
                stale = page.request.post(url + "/clarifications", data={**reply, "text": text})
                assert stale.status == 409
            assert page.request.post(url + "/arrangement-requests", data=submission).status == 409
            assert page.request.post(url + "/confirm", data={}).status == 404
            page.reload()
            assert page.request.post(url + "/clarifications", data=reply).status == 409
            assert len(evidence.calls) == call_count
            assert len(evidence.commands) == 1
        finally:
            browser.close()
