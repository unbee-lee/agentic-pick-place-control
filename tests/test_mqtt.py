"""Real-broker tests, including a separate simulator and browser process."""

import asyncio
from contextlib import contextmanager, nullcontext
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from typing import Iterator, Mapping
from urllib.error import URLError
from urllib.request import urlopen
from uuid import uuid4

import aiomqtt
import pytest
from playwright.sync_api import expect, sync_playwright

from ucs_app.controlled import ControlledRobotExecutionStation, ControlledRobotCommandAgent, ControlledUserCommandAgent
from ucs_app.interfaces import ControllerUpdate
from ucs_app.mqtt import COMMAND_TOPIC, STATUS_TOPIC, RESULT_TOPIC, MqttConfig, MqttRobotExecutionStation
from ucs_app.sessions import SessionStore
from ucs_app.transport import utc_timestamp
from ucs_app.workflow import UcsWorkflow, WorkflowConflict, WorkflowFailure
from ucs_contracts import validate_message

TARGET: dict[str, object] = {"E": "front_left", "B": "front_center", "H": "front_right"}


def command() -> dict[str, object]:
    return {"schema_version": "1.0", "message_id": str(uuid4()), "type": "ARRANGE",
            "target_positions": dict(TARGET), "created_at": utc_timestamp()}


def free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def environment(port: int) -> dict[str, str]:
    return {**{k: v for k, v in os.environ.items() if not k.startswith("UCS_")},
            "UCS_MQTT_HOST": "127.0.0.1", "UCS_MQTT_PORT": str(port),
            "UCS_MQTT_OPERATION_TIMEOUT": "1", "UCS_MQTT_EXECUTION_TIMEOUT": "2",
            "UCS_RES_DELAY": "0.15"}


@contextmanager
def process(args: list[str], log: Path, env: dict[str, str]) -> Iterator[subprocess.Popen[bytes]]:
    with log.open("wb") as output:
        child = subprocess.Popen(args, stdout=output, stderr=subprocess.STDOUT, env=env)
        try:
            yield child
        finally:
            if child.poll() is None:
                child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)


@pytest.fixture
def broker(tmp_path: Path) -> Iterator[tuple[int, subprocess.Popen[bytes]]]:
    executable = shutil.which("mosquitto")
    if executable is None:
        pytest.fail("Real MQTT tests require mosquitto on PATH; see docs/mqtt-res.md")
    port = free_port()
    config = tmp_path / "mosquitto.conf"
    config.write_text(f"listener {port} 127.0.0.1\nallow_anonymous true\npersistence false\n")
    with process([executable, "-c", str(config)], tmp_path / "broker.log", environment(port)) as broker:
        for _ in range(100):
            assert broker.poll() is None, "Broker exited during startup"
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.02)
        else:
            pytest.fail("Broker did not start")
        yield port, broker


@pytest.fixture
def broker_port(broker: tuple[int, subprocess.Popen[bytes]]) -> int:
    return broker[0]


@contextmanager
def simulator(port: int, tmp_path: Path) -> Iterator[subprocess.Popen[bytes]]:
    log = tmp_path / "simulator.log"
    with process([sys.executable, "-m", "ucs_app.simulated_res"], log, environment(port)) as child:
        for _ in range(200):
            assert child.poll() is None, "Simulator exited during startup"
            if "Simulated RES ready" in log.read_text():
                break
            time.sleep(0.02)
        else:
            pytest.fail("Simulator did not subscribe")
        yield child


async def collect(res: MqttRobotExecutionStation, payload: Mapping[str, object]) -> list[ControllerUpdate]:
    return [update async for update in res.execute(payload)]


def test_transport_receives_correlated_simulated_result(broker_port: int, tmp_path: Path) -> None:
    with simulator(broker_port, tmp_path):
        async def run() -> None:
            config = MqttConfig(port=broker_port)
            payload = command()
            async with config.client() as observer:
                await observer.subscribe(COMMAND_TOPIC, qos=1)
                updates = await asyncio.wait_for(collect(MqttRobotExecutionStation(config), payload), 5)
                published = await asyncio.wait_for(observer.messages.__anext__(), 1)
                assert json.loads(bytes(published.payload)) == payload
            assert [update.message_type for update in updates] == ["status", "result"]
            for update in updates:
                validate_message(update.message_type, update.payload)
                assert update.payload["message_id"] == payload["message_id"]
            assert updates[-1].payload["execution"] == {"status": "COMPLETED", "error": None}
            assert updates[-1].payload["verification"] == {
                "status": "NOT_RUN", "outcome": None, "observed_positions": None, "error": None}
        asyncio.run(run())


@pytest.mark.parametrize("invalid", ["schema", "arrangement"])
def test_simulator_independently_rejects_invalid_commands(broker_port: int, tmp_path: Path, invalid: str) -> None:
    with simulator(broker_port, tmp_path):
        async def run() -> None:
            payload = command()
            if invalid == "schema":
                payload["extra"] = "forbidden"
            else:
                payload["target_positions"] = {**TARGET, "B": "front_left"}
            async with MqttConfig(port=broker_port).client() as client:
                await client.subscribe([(STATUS_TOPIC, 1), (RESULT_TOPIC, 1)])
                await client.publish(COMMAND_TOPIC, json.dumps(payload), qos=1)
                reply = await asyncio.wait_for(client.messages.__anext__(), 2)
                assert str(reply.topic) == RESULT_TOPIC  # No BUSY / execution attempt.
                result = json.loads(bytes(reply.payload))
                validate_message("result", result)
                assert result["message_id"] == payload["message_id"]
                assert result["execution"]["status"] == "NOT_STARTED"
                assert result["execution"]["error"]["code"] == "INVALID_COMMAND"
        asyncio.run(run())


def test_duplicate_ids_and_malformed_commands_do_not_execute(broker_port: int, tmp_path: Path) -> None:
    with simulator(broker_port, tmp_path):
        async def run() -> None:
            first, marker = command(), command()
            async with MqttConfig(port=broker_port).client() as client:
                await client.subscribe([(STATUS_TOPIC, 1), (RESULT_TOPIC, 1)])
                for payload in [json.dumps(first), json.dumps(first), json.dumps({**first, "extra": True}),
                                "broken json", '{"message_id":"not-a-uuid"}',
                                json.dumps({"message_id": "{" + str(uuid4()) + "}"}), json.dumps(marker)]:
                    await client.publish(COMMAND_TOPIC, payload, qos=1)
                results: list[dict[str, object]] = []
                async def receive() -> None:
                    async for message in client.messages:
                        payload = json.loads(bytes(message.payload))
                        results.append(payload)
                        if str(message.topic) == RESULT_TOPIC and payload["message_id"] == marker["message_id"]:
                            return
                await asyncio.wait_for(receive(), 3)
                assert [p["message_id"] for p in results] == [first["message_id"], first["message_id"],
                                                            marker["message_id"], marker["message_id"]]
        asyncio.run(run())


def test_retained_command_is_not_replayed_on_simulator_start(broker_port: int, tmp_path: Path) -> None:
    async def run() -> None:
        stale, fresh = command(), command()
        async with MqttConfig(port=broker_port).client() as client:
            await client.subscribe([(STATUS_TOPIC, 1), (RESULT_TOPIC, 1)])
            await client.publish(COMMAND_TOPIC, json.dumps(stale), qos=1, retain=True)
            with simulator(broker_port, tmp_path):
                await client.publish(COMMAND_TOPIC, json.dumps(fresh), qos=1, retain=False)
                async def receive() -> list[dict[str, object]]:
                    replies = []
                    async for message in client.messages:
                        payload = json.loads(bytes(message.payload))
                        replies.append(payload)
                        if str(message.topic) == RESULT_TOPIC and payload["message_id"] == fresh["message_id"]:
                            return replies
                    raise AssertionError("No terminal result")
                replies = await asyncio.wait_for(receive(), 3)
                assert len(replies) == 2 and all(p["message_id"] == fresh["message_id"] for p in replies)
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["uncorrelated", "invalid", "timeout"])
def test_workflow_filters_or_guards_transport_failures(broker_port: int, mode: str) -> None:
    async def run() -> None:
        config = MqttConfig(port=broker_port, operation_timeout=0.5)
        store = SessionStore()
        session = store.create()
        workflow = UcsWorkflow(user_command_agent=ControlledUserCommandAgent(),
                               robot_command_agent=ControlledRobotCommandAgent(),
                               robot_execution_station=MqttRobotExecutionStation(config), sessions=store,
                               execution_timeout=0.5)
        async with config.client() as peer:
            await peer.subscribe(COMMAND_TOPIC, qos=1)
            task = asyncio.create_task(workflow.submit(session, "elephant left, bear centre, hippo right", "one"))
            incoming = await asyncio.wait_for(peer.messages.__anext__(), 2)
            payload = json.loads(bytes(incoming.payload))
            if mode == "uncorrelated":
                async for update in ControlledRobotExecutionStation(result_delay_seconds=0).execute(command()):
                    await peer.publish(RESULT_TOPIC if update.message_type == "result" else STATUS_TOPIC,
                                       json.dumps(dict(update.payload)), qos=1)
                async for update in ControlledRobotExecutionStation(result_delay_seconds=0).execute(payload):
                    await peer.publish(RESULT_TOPIC if update.message_type == "result" else STATUS_TOPIC,
                                       json.dumps(dict(update.payload)), qos=1)
                await task
                assert session.active_command is None
            else:
                if mode == "invalid":
                    await peer.publish(RESULT_TOPIC, json.dumps({"message_id": payload["message_id"]}), qos=1)
                with pytest.raises(WorkflowFailure, match="outcome is unknown"):
                    await task
                assert session.active_command is not None
                with pytest.raises(WorkflowConflict):
                    await workflow.submit(session, "elephant left, bear centre, hippo right", "two")
    asyncio.run(run())


@pytest.fixture
def mqtt_url(broker_port: int, tmp_path: Path, request: pytest.FixtureRequest) -> Iterator[str]:
    port = free_port()
    url = f"http://127.0.0.1:{port}"
    res_context = nullcontext() if getattr(request, "param", "success") == "missing" else simulator(broker_port, tmp_path)
    with res_context, process(
        [sys.executable, "-m", "uvicorn", "ucs_app.mqtt:create_mqtt_app", "--factory",
         "--host", "127.0.0.1", "--port", str(port)], tmp_path / "app.log", environment(broker_port)
    ) as app:
        for _ in range(200):
            assert app.poll() is None, "UCS exited during startup"
            try:
                with urlopen(url + "/health", timeout=0.1):
                    break
            except (OSError, URLError):
                time.sleep(0.02)
        else:
            pytest.fail("UCS did not start")
        yield url


def test_browser_dispatch_and_clarification_over_real_mqtt(mqtt_url: str) -> None:
    from itertools import permutations
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(mqtt_url)
            expect(page.get_by_test_id("ucs-status")).to_have_text("Ready")
            for positions in permutations(["left", "centre", "right"]):
                page.get_by_test_id("arrangement-request").fill(", ".join(
                    f"{animal} {position}" for animal, position in zip(["elephant", "bear", "hippo"], positions)))
                with page.expect_response("**/arrangement-requests") as submitted:
                    page.get_by_role("button", name="Submit request").click()
                assert submitted.value.status == 202
                expect(page.get_by_test_id("ucs-status")).to_have_text("Completed")
                expect(page.locator('[data-event-kind="command_publication"]')).to_have_count(1)
                expect(page.locator('[data-event-kind="progress"]')).to_have_count(1)
                expect(page.get_by_test_id("status-description")).to_contain_text("Physical placement was not verified")
            page.get_by_test_id("arrangement-request").fill("elephant left")
            page.get_by_role("button", name="Submit request").click()
            expect(page.get_by_test_id("ucs-status")).to_have_text("Needs clarification")
            expect(page.locator('[data-event-kind="command_publication"]')).to_have_count(0)
            page.get_by_test_id("arrangement-request").fill("bear centre, hippo right")
            page.get_by_role("button", name="Send answer").click()
            expect(page.get_by_test_id("ucs-status")).to_have_text("Completed")
            expect(page.locator('[data-event-kind="command_publication"]')).to_have_count(1)
            expect(page.get_by_role("button", name="Confirm", exact=True)).to_have_count(0)
        finally:
            browser.close()


def test_broker_loss_preserves_unknown_guard(broker: tuple[int, subprocess.Popen[bytes]]) -> None:
    async def run() -> None:
        config = MqttConfig(port=broker[0], operation_timeout=0.5)
        store = SessionStore()
        session = store.create()
        workflow = UcsWorkflow(user_command_agent=ControlledUserCommandAgent(),
                               robot_command_agent=ControlledRobotCommandAgent(),
                               robot_execution_station=MqttRobotExecutionStation(config), sessions=store,
                               execution_timeout=2)
        async with config.client() as observer:
            await observer.subscribe(COMMAND_TOPIC, qos=1)
            task = asyncio.create_task(workflow.submit(session, "elephant left, bear centre, hippo right", "one"))
            await asyncio.wait_for(observer.messages.__anext__(), 1)
            broker[1].terminate()
            with pytest.raises(aiomqtt.MqttError):
                await asyncio.wait_for(observer.messages.__anext__(), 2)
            with pytest.raises(WorkflowFailure, match="outcome is unknown"):
                await task
            assert session.active_command is not None
            with pytest.raises(WorkflowConflict):
                await workflow.submit(session, "elephant left, bear centre, hippo right", "two")
    asyncio.run(run())


@pytest.mark.parametrize("mqtt_url", ["missing"], indirect=True)
def test_browser_missing_simulator_does_not_resend_after_refresh(mqtt_url: str) -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(mqtt_url)
            expect(page.get_by_test_id("ucs-status")).to_have_text("Ready")
            page.get_by_test_id("arrangement-request").fill("elephant left, bear centre, hippo right")
            page.get_by_role("button", name="Submit request").click()
            expect(page.get_by_test_id("ucs-status")).to_have_text("Outcome unknown")
            page.reload()
            expect(page.get_by_test_id("ucs-status")).to_have_text("Outcome unknown")
            expect(page.get_by_role("button", name="Submit request")).to_be_disabled()
            expect(page.locator('[data-event-kind="command_publication"]')).to_have_count(1)
        finally:
            browser.close()


def test_retained_result_cannot_complete_new_execution(broker_port: int) -> None:
    async def run() -> None:
        config = MqttConfig(port=broker_port)
        payload = command()
        async with config.client() as peer:
            async for update in ControlledRobotExecutionStation(result_delay_seconds=0).execute(payload):
                if update.message_type == "result":
                    await peer.publish(RESULT_TOPIC, json.dumps(dict(update.payload)), qos=1, retain=True)
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(collect(MqttRobotExecutionStation(config), payload), 0.3)
    asyncio.run(run())


def test_concurrent_requests_receive_only_their_own_results(broker_port: int, tmp_path: Path) -> None:
    with simulator(broker_port, tmp_path):
        async def run() -> None:
            res = MqttRobotExecutionStation(MqttConfig(port=broker_port))
            first, second = command(), command()
            second["target_positions"] = {**TARGET, "E": "front_right", "H": "front_left"}
            replies = await asyncio.wait_for(asyncio.gather(collect(res, first), collect(res, second)), 3)
            for payload, updates in zip([first, second], replies):
                assert len(updates) == 2
                assert all(u.payload["message_id"] == payload["message_id"] for u in updates)
        asyncio.run(run())
