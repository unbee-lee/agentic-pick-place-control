"""Durable recovery uses real workflow boundaries, never a dispatch replay."""

import asyncio
from contextlib import contextmanager, suppress
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from typing import Any, Iterator, Mapping
from uuid import uuid4

import httpx
from playwright.sync_api import expect, sync_playwright
import pytest

from ucs_app.controlled import ControlledRobotCommandAgent, ControlledUserCommandAgent
from ucs_app.interfaces import AgentContext
from ucs_app.mqtt import MqttConfig, MqttRobotExecutionStation, RESULT_TOPIC
from ucs_app.recovery import RecoveryJournal
from ucs_app.sessions import SessionStore
from ucs_app.workflow import UcsWorkflow, WorkflowConflict, WorkflowFailure
from test_mqtt import broker, broker_port, command, environment, free_port, process, simulator
from test_workflow import RecordingRES


async def snapshot(client: httpx.AsyncClient) -> dict[str, Any]:
    async with client.stream("GET", "/events") as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                value: dict[str, Any] = json.loads(line[6:])
                return value
    raise AssertionError("Missing snapshot")


def workflow(store: SessionStore, res: RecordingRES) -> UcsWorkflow:
    return UcsWorkflow(user_command_agent=ControlledUserCommandAgent(),
                       robot_command_agent=ControlledRobotCommandAgent(),
                       robot_execution_station=res, sessions=store)


def test_clarification_restart_preserves_context_limits_and_ownership(tmp_path: Path) -> None:
    async def run() -> None:
        path = str(tmp_path / "ucs.sqlite")
        store, res = SessionStore(path), RecordingRES()
        session = store.create()
        identity = session.session_id
        await workflow(store, res).submit(session, "elephant left", "request")
        steps = session.agent_steps
        store.close()
        store = SessionStore(path)
        restored = store.get(identity)
        assert restored is not None and restored.awaiting_reply
        assert restored.agent_steps == steps and restored.clarification_turn == 1
        runner = workflow(store, res)
        with pytest.raises(WorkflowConflict):
            await runner.answer(store.create(), "request", 1, "bear centre, hippo right")
        with pytest.raises(WorkflowConflict):
            await runner.submit(store.create(), "elephant left", "request")
        await runner.answer(restored, "request", 1, "bear centre, hippo right")
        assert len(res.commands) == 1
        assert res.commands[0]["target_positions"] == {"E": "front_left", "B": "front_center", "H": "front_right"}
        store.close()
        store = SessionStore(path)
        restored = store.get(identity)
        assert restored is not None and restored.active_command is None
        with pytest.raises(WorkflowConflict):
            await workflow(store, res).answer(restored, "request", 1, "thanks")
        assert len(res.commands) == 1
        store.close()
    asyncio.run(run())


def test_restart_does_not_reset_clarification_budget_or_cancelled_request(tmp_path: Path) -> None:
    async def run() -> None:
        path = str(tmp_path / "ucs.sqlite")
        store, res = SessionStore(path), RecordingRES()
        session = store.create()
        identity = session.session_id
        await workflow(store, res).submit(session, "elephant left", "request")
        for turn in (1, 2):
            store.close()
            store = SessionStore(path)
            session = store.get(identity)  # type: ignore[assignment]
            assert session is not None
            await workflow(store, res).answer(session, "request", turn, "elephant left")
        with pytest.raises(WorkflowFailure):
            await workflow(store, res).answer(session, "request", 3, "elephant left")
        assert session.agent_steps == 4 and not res.commands and not session.awaiting_reply
        await workflow(store, res).submit(session, "elephant left", "cancelled")
        await workflow(store, res).cancel(session, "cancelled", 1)
        store.close()
        store = SessionStore(path)
        restored = store.get(identity)
        assert restored is not None
        with pytest.raises(WorkflowConflict):
            await workflow(store, res).answer(restored, "cancelled", 1, "bear centre, hippo right")
        assert not res.commands
        store.close()
    asyncio.run(run())


@pytest.mark.parametrize("phase", ["interpretation", "repair"])
def test_crash_snapshot_stops_interrupted_model_work(tmp_path: Path, phase: str) -> None:
    async def run() -> None:
        entered = asyncio.Event()
        class PausedUser(ControlledUserCommandAgent):
            async def decide(self, context: AgentContext) -> Mapping[str, object]:
                if phase == "interpretation":
                    entered.set()
                    await asyncio.Event().wait()
                return await super().decide(context)
        class PausedRepair(ControlledRobotCommandAgent):
            async def decide(self, context: AgentContext) -> Mapping[str, object]:
                if context.feedback:
                    entered.set()
                    await asyncio.Event().wait()
                return {"action": "VALIDATE", "command": {}}
        store, res = SessionStore(str(tmp_path / "source.sqlite")), RecordingRES()
        session = store.create()
        runner = UcsWorkflow(user_command_agent=PausedUser(), robot_command_agent=PausedRepair(),
                             robot_execution_station=res, sessions=store)
        task = asyncio.create_task(runner.submit(session, "elephant left, bear centre, hippo right", "request"))
        await asyncio.wait_for(entered.wait(), 2)
        assert store.journal is not None
        # A transactionally consistent disk image at the interrupted external-call boundary.
        with sqlite3.connect(str(tmp_path / "crash.sqlite")) as copy:
            store.journal.db.backup(copy)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        store.close()
        recovered = SessionStore(str(tmp_path / "crash.sqlite"))
        restored = recovered.get(session.session_id)
        assert restored is not None and restored.workflow_id is None
        assert not restored.awaiting_reply and not restored.request_in_progress
        assert not res.commands
        assert any("Nothing was sent" in event.message for event in restored._events)
        with pytest.raises(WorkflowConflict):
            await workflow(recovered, res).submit(restored, "elephant left", "request")
        recovered.close()
    asyncio.run(run())


def test_journal_rejects_second_owner(tmp_path: Path) -> None:
    journal = RecoveryJournal(str(tmp_path / "ucs.sqlite"))
    try:
        with pytest.raises(BlockingIOError):
            RecoveryJournal(str(tmp_path / "ucs.sqlite"))
    finally:
        journal.close()


@contextmanager
def application(tmp_path: Path, port: int, broker_port: int) -> Iterator[subprocess.Popen[bytes]]:
    env = {**environment(broker_port), "UCS_STATE_PATH": str(tmp_path / "ucs.sqlite")}
    with process([sys.executable, "-m", "uvicorn", "ucs_app.mqtt:create_mqtt_app", "--factory",
                  "--host", "127.0.0.1", "--port", str(port)], tmp_path / "app.log", env) as child:
        for _ in range(200):
            assert child.poll() is None, (tmp_path / "app.log").read_text()
            try:
                health = httpx.get(f"http://127.0.0.1:{port}/health", timeout=0.1, trust_env=False)
                if health.json()["result_listener"] == "ready":
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.02)
        else:
            pytest.fail("Recovery app not ready")
        yield child


def test_browser_clarification_survives_process_restart(broker_port: int, tmp_path: Path) -> None:
    port = free_port()
    with simulator(broker_port, tmp_path), sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            with application(tmp_path, port, broker_port) as child:
                page.goto(f"http://127.0.0.1:{port}")
                expect(page.get_by_test_id("ucs-status")).to_have_text("Ready")
                page.get_by_test_id("arrangement-request").fill("elephant left")
                page.get_by_role("button", name="Submit request").click()
                expect(page.get_by_test_id("ucs-status")).to_have_text("Needs clarification")
                child.kill()
                child.wait(timeout=5)
            with application(tmp_path, port, broker_port):
                page.reload()
                expect(page.get_by_test_id("ucs-status")).to_have_text("Needs clarification")
                page.get_by_test_id("arrangement-request").fill("bear centre, hippo right")
                page.get_by_role("button", name="Send answer").click()
                expect(page.get_by_test_id("ucs-status")).to_have_text("Completed")
                expect(page.locator('[data-event-kind="command_publication"]')).to_have_count(1)
                page.reload()
                # Recovery must release the guard regardless of the demo's chosen
                # completed-page presentation (Completed versus Ready on reload).
                expect(page.get_by_role("button", name="Submit request")).to_be_enabled()
                expect(page.get_by_role("button", name="Send answer")).to_have_count(0)
        finally:
            browser.close()


@pytest.mark.parametrize("phase", ["execution", "unknown"])
def test_late_result_queued_during_restart_reconciles_without_resend(
    broker_port: int, tmp_path: Path, phase: str,
) -> None:
    from ucs_app.controlled import ControlledRobotExecutionStation
    from ucs_app.mqtt import COMMAND_TOPIC

    async def run() -> None:
        port = free_port()
        async with MqttConfig(port=broker_port).client() as peer, httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}", timeout=10, trust_env=False,
        ) as client:
            await peer.subscribe(COMMAND_TOPIC, qos=1)
            request_id = str(uuid4())
            with application(tmp_path, port, broker_port) as child:
                await client.get("/")
                task = asyncio.create_task(client.post("/arrangement-requests", json={
                    "text": "elephant left, bear centre, hippo right", "request_id": request_id}))
                incoming = await asyncio.wait_for(peer.messages.__anext__(), 3)
                payload = json.loads(bytes(incoming.payload))
                with sqlite3.connect(str(tmp_path / "ucs.sqlite")) as db:
                    stored = db.execute("SELECT command FROM dispatches WHERE id=?", (payload["message_id"],)).fetchone()
                assert json.loads(stored[0]) == payload  # Committed before publication.
                if phase == "unknown":
                    assert (await task).status_code == 503
                    assert (await snapshot(client))["outcome_unknown"]
                child.kill()
                child.wait(timeout=5)
                with suppress(httpx.HTTPError):
                    await task
            # The broker queues QoS1 results for the stable offline recovery subscriber.
            await peer.publish(RESULT_TOPIC, json.dumps({"message_id": payload["message_id"]}), qos=1)
            result = {}
            async for update in ControlledRobotExecutionStation(result_delay_seconds=0).execute(payload):
                if update.message_type == "result":
                    result = dict(update.payload)
            for _ in range(2):
                await peer.publish(RESULT_TOPIC, json.dumps(result), qos=1)
            with application(tmp_path, port, broker_port):
                for _ in range(50):
                    state = await snapshot(client)
                    if not state["active"]:
                        break
                    await asyncio.sleep(0.05)
                assert not state["active"] and not state["outcome_unknown"]
                results = [event for event in state["events"] if event["kind"] == "result"]
                assert len(results) == 1 and results[0]["details"]["reconciled"]
                assert results[0]["details"]["message_id"] == payload["message_id"]
                assert (await client.post("/arrangement-requests", json={
                    "text": "elephant left", "request_id": request_id})).status_code == 409
                assert (await client.post("/clarifications", json={
                    "text": "thanks", "workflow_id": request_id, "turn": 1})).status_code == 409
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(peer.messages.__anext__(), 0.2)
    asyncio.run(run())


def test_listener_reconnect_and_late_result_after_timeout(broker_port: int, tmp_path: Path) -> None:
    from ucs_app.controlled import ControlledRobotExecutionStation
    from ucs_app.mqtt import COMMAND_TOPIC

    async def run() -> None:
        store = SessionStore(str(tmp_path / "ucs.sqlite"))
        res = MqttRobotExecutionStation(MqttConfig(port=broker_port))
        listener = asyncio.create_task(res.listen_results(store))
        try:
            for _ in range(100):
                if store.listener_status == "ready":
                    break
                await asyncio.sleep(0.02)
            assert store.listener_status == "ready"
            runner = UcsWorkflow(user_command_agent=ControlledUserCommandAgent(),
                                 robot_command_agent=ControlledRobotCommandAgent(),
                                 robot_execution_station=res, sessions=store, execution_timeout=0.1)
            session = store.create()
            async with MqttConfig(port=broker_port).client() as peer:
                await peer.subscribe(COMMAND_TOPIC, qos=1)
                with pytest.raises(WorkflowFailure):
                    await runner.submit(session, "elephant left, bear centre, hippo right", "one")
                incoming = await asyncio.wait_for(peer.messages.__anext__(), 1)
                payload = json.loads(bytes(incoming.payload))
                assert session.active_command is not None
                # Stop/start only the receive-only listener; its durable subscription remains.
                listener.cancel()
                with suppress(asyncio.CancelledError):
                    await listener
                for cmd in (command(), payload, payload):
                    async for update in ControlledRobotExecutionStation(result_delay_seconds=0).execute(cmd):
                        if update.message_type == "result":
                            await peer.publish(RESULT_TOPIC, json.dumps(dict(update.payload)), qos=1)
                listener = asyncio.create_task(res.listen_results(store))
                for _ in range(100):
                    if session.active_command is None:
                        break
                    await asyncio.sleep(0.02)
                assert session.active_command is None
                assert len([e for e in session._events if e.kind == "result"]) == 1
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(peer.messages.__anext__(), 0.1)
        finally:
            listener.cancel()
            with suppress(asyncio.CancelledError):
                await listener
            store.close()
    asyncio.run(run())


@pytest.mark.parametrize("interrupted", [False, True])
def test_simulator_reserves_identity_across_restart(tmp_path: Path, interrupted: bool) -> None:
    from unittest.mock import MagicMock
    import aiomqtt
    from ucs_app.simulated_res import SimulatedRES, SimulationConfig

    async def run() -> None:
        path = str(tmp_path / "res.sqlite")
        payload = command()
        message = MagicMock(spec=aiomqtt.Message)
        message.retain = False
        message.payload = json.dumps(payload).encode()
        client = MagicMock(spec=aiomqtt.Client)
        first = SimulatedRES(SimulationConfig(delay_seconds=30 if interrupted else 0), path)
        if interrupted:
            task = asyncio.create_task(first.handle(client, message))
            for _ in range(100):
                if client.publish.await_count:
                    break
                await asyncio.sleep(0.01)
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        else:
            await first.handle(client, message)
        expected = 1 if interrupted else 2
        assert client.publish.await_count == expected
        first.close()
        second = SimulatedRES(SimulationConfig(delay_seconds=0), path)
        await second.handle(client, message)
        message.payload = json.dumps({**payload, "extra": True}).encode()
        await second.handle(client, message)
        assert client.publish.await_count == expected
        second.close()
    asyncio.run(run())


@pytest.mark.parametrize("outage_delay,stop_before_broker", [(0, False), (0.3, False), (0.6, True)])
def test_broker_outage_reconnects_receive_only_listener(
    broker: tuple[int, subprocess.Popen[bytes]], tmp_path: Path,
    outage_delay: float, stop_before_broker: bool,
) -> None:
    import shutil
    import gc
    from ucs_app.mqtt import COMMAND_TOPIC

    unhandled: list[dict[str, Any]] = []
    from ucs_app.controlled import ControlledRobotExecutionStation

    async def run() -> None:
        asyncio.get_running_loop().set_exception_handler(lambda loop, context: unhandled.append(context))
        store = SessionStore(str(tmp_path / "ucs.sqlite"))
        config = MqttConfig(port=broker[0], operation_timeout=0.2)
        res = MqttRobotExecutionStation(config)
        listener = asyncio.create_task(res.listen_results(store))
        try:
            for _ in range(100):
                if store.listener_status == "ready":
                    break
                await asyncio.sleep(0.02)
            session = store.create()
            runner = UcsWorkflow(user_command_agent=ControlledUserCommandAgent(),
                                 robot_command_agent=ControlledRobotCommandAgent(),
                                 robot_execution_station=res, sessions=store, execution_timeout=0.1)
            with pytest.raises(WorkflowFailure):
                await runner.submit(session, "elephant left, bear centre, hippo right", "one")
            payload = session.workflow_state["candidate"]
            await asyncio.sleep(outage_delay)
            broker[1].terminate()
            broker[1].wait(timeout=5)
            for _ in range(100):
                if store.listener_status == "disconnected":
                    break
                await asyncio.sleep(0.02)
            assert store.listener_status == "disconnected" and session.active_command is not None
            executable = shutil.which("mosquitto")
            assert executable is not None
            with process([executable, "-c", str(tmp_path / "mosquitto.conf")],
                         tmp_path / "restarted-broker.log", environment(broker[0])):
                for _ in range(200):
                    if store.listener_status == "ready":
                        break
                    await asyncio.sleep(0.02)
                assert store.listener_status == "ready"
                async with config.client() as peer:
                    await peer.subscribe(COMMAND_TOPIC, qos=1)
                    assert session.active_command is not None
                    async for update in ControlledRobotExecutionStation(result_delay_seconds=0).execute(payload):
                        if update.message_type == "result":
                            await peer.publish(RESULT_TOPIC, json.dumps(dict(update.payload)), qos=1)
                    for _ in range(100):
                        if session.active_command is None:
                            break
                        await asyncio.sleep(0.02)
                    assert session.active_command is None
                    with pytest.raises(asyncio.TimeoutError):
                        await asyncio.wait_for(peer.messages.__anext__(), 0.1)
                if stop_before_broker:
                    started = time.monotonic()
                    listener.cancel()
                    with suppress(asyncio.CancelledError):
                        await listener
                    assert time.monotonic() - started < 1
                    assert store.listener_status == "stopped"
        finally:
            listener.cancel()
            with suppress(asyncio.CancelledError):
                await listener
            store.close()
    asyncio.run(run())
    gc.collect()
    assert not unhandled, [context["message"] for context in unhandled]


def test_unknown_without_result_remains_blocked_after_restart(tmp_path: Path) -> None:
    from unittest.mock import patch

    async def run() -> None:
        path = str(tmp_path / "ucs.sqlite")
        store, res = SessionStore(path), RecordingRES()
        session = store.create()
        payload = command()
        session.workflow_id = str(uuid4())
        session.seen_request_ids.add(session.workflow_id)
        session.workflow_state = {"assignment_revision": 1}
        store.reserve(session, payload)
        store.close()
        store = SessionStore(path)
        restored = store.get(session.session_id)
        assert restored is not None and restored.public_state()["outcome_unknown"]
        store.reconcile()
        runner = workflow(store, res)
        with pytest.raises(WorkflowConflict):
            await runner.submit(restored, "elephant left", "new")
        with pytest.raises(WorkflowConflict):
            await runner.cancel(restored, str(restored.workflow_id), 1)
        assert not res.commands
        store.close()
        # A failed durable reservation must prevent every external execution attempt.
        store = SessionStore(str(tmp_path / "broken.sqlite"))
        with patch.object(RecoveryJournal, "reserve", side_effect=sqlite3.OperationalError("disk full")):
            with pytest.raises(WorkflowFailure):
                await workflow(store, res).submit(store.create(), "elephant left, bear centre, hippo right", "fresh")
        assert not res.commands
        store.close()
    asyncio.run(run())


def test_failed_reservation_releases_session_and_survives_restart(tmp_path: Path) -> None:
    from unittest.mock import patch

    async def run() -> None:
        path = str(tmp_path / "failed-reservation.sqlite")
        store, res = SessionStore(path), RecordingRES()
        session = store.create()
        with patch.object(RecoveryJournal, "reserve", side_effect=sqlite3.OperationalError("disk full")):
            with pytest.raises(WorkflowFailure, match="Nothing was sent"):
                await workflow(store, res).submit(session, "elephant left, bear centre, hippo right", "failed")
        assert not res.commands and session.active_command is None
        assert not session.public_state()["outcome_unknown"]
        store.close()
        store = SessionStore(path)
        restored = store.get(session.session_id)
        assert restored is not None and restored.active_command is None
        with pytest.raises(WorkflowConflict):
            await workflow(store, res).submit(restored, "elephant left", "failed")
        await workflow(store, res).submit(restored, "elephant left, bear centre, hippo right", "fresh")
        assert len(res.commands) == 1
        store.close()
    asyncio.run(run())


@pytest.mark.parametrize("boundary", ["before_commit", "after_commit", "legacy_snapshot"])
def test_terminal_snapshot_crash_boundaries(tmp_path: Path, boundary: str) -> None:
    from unittest.mock import patch

    async def run() -> None:
        path = str(tmp_path / "source.sqlite")
        recovered_path = str(tmp_path / "crash.sqlite")
        store, res = SessionStore(path), RecordingRES()
        session = store.create()
        assert store.journal is not None
        original = store.journal.settle
        def capture(message_id: str, session_id: str, data: dict[str, Any], result: dict[str, object]) -> None:
            assert store.journal is not None
            if boundary == "before_commit":
                with sqlite3.connect(recovered_path) as copied:
                    store.journal.db.backup(copied)
                raise sqlite3.OperationalError("terminal commit failed")
            original(message_id, session_id, data, result)
            if boundary == "legacy_snapshot":
                # Previously-written snapshot: terminal event and no workflow, but processing=true.
                old = dict(data)
                old["request_in_progress"] = True
                store.journal.save(session_id, old)
            with sqlite3.connect(recovered_path) as copied:
                store.journal.db.backup(copied)
        with patch.object(store.journal, "settle", side_effect=capture):
            if boundary == "before_commit":
                with pytest.raises(WorkflowFailure, match="unknown"):
                    await workflow(store, res).submit(session, "elephant left, bear centre, hippo right", "one")
                assert session.active_command is not None
                assert not any(event.kind == "result" for event in session._events)
            else:
                await workflow(store, res).submit(session, "elephant left, bear centre, hippo right", "one")
        assert len(res.commands) == 1
        store.close()
        recovered = SessionStore(recovered_path)
        restored = recovered.get(session.session_id)
        assert restored is not None
        assert not any("Nothing was sent" in event.message for event in restored._events)
        if boundary == "before_commit":
            assert restored.active_command is not None
        else:
            assert restored.active_command is None and restored.last_successful_arrangement is not None
            assert len([event for event in restored._events if event.kind == "result"]) == 1
        with pytest.raises(WorkflowConflict):
            await workflow(recovered, res).submit(restored, "elephant left", "one")
        assert len(res.commands) == 1
        recovered.close()
    asyncio.run(run())
