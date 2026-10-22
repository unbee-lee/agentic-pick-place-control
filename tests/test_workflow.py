"""Exercise delivery and clarification through the workflow's public interface."""

import asyncio
from itertools import permutations
from typing import AsyncIterator, Mapping

import pytest

from ucs_app.controlled import ControlledRobotCommandAgent, ControlledRobotExecutionStation, ControlledUserCommandAgent
from ucs_app.interfaces import AgentContext, ControllerUpdate
from ucs_app.sessions import SessionStore
from ucs_app.workflow import UcsWorkflow, WorkflowConflict, WorkflowFailure

TARGET: dict[str, object] = {"E": "front_left", "B": "front_center", "H": "front_right"}


class RecordingRES(ControlledRobotExecutionStation):
    def __init__(self) -> None:
        super().__init__(result_delay_seconds=0)
        self.commands: list[Mapping[str, object]] = []

    async def execute(self, command: Mapping[str, object]) -> AsyncIterator[ControllerUpdate]:
        self.commands.append(command)
        async for update in super().execute(command):
            yield update


class RecordingCommandAgent(ControlledUserCommandAgent):
    def __init__(self) -> None:
        self.contexts: list[AgentContext] = []

    async def decide(self, context: AgentContext) -> Mapping[str, object]:
        self.contexts.append(context)
        return await super().decide(context)


def composition() -> tuple[UcsWorkflow, SessionStore, RecordingRES, RecordingCommandAgent]:
    store = SessionStore()
    res = RecordingRES()
    command = RecordingCommandAgent()
    workflow = UcsWorkflow(user_command_agent=command, robot_command_agent=ControlledRobotCommandAgent(),
                           robot_execution_station=res, sessions=store)
    return workflow, store, res, command


@pytest.mark.parametrize("positions", list(permutations(["left", "centre", "right"])))
def test_each_valid_permutation_dispatches_exactly_once(positions: tuple[str, ...]) -> None:
    async def run() -> None:
        workflow, store, res, _ = composition()
        session = store.create()
        text = ", ".join(f"{animal} {position}" for animal, position in zip(["elephant", "bear", "hippo"], positions))
        await workflow.submit(session, text, "request-1")
        assert len(res.commands) == 1
        expected = dict(zip(["E", "B", "H"], ["front_" + p.replace("centre", "center") for p in positions]))
        assert res.commands[0]["target_positions"] == expected
        assert session.active_command is None
        with pytest.raises(WorkflowConflict):
            await workflow.submit(session, text, "request-1")
        assert len(res.commands) == 1
    asyncio.run(run())


@pytest.mark.parametrize("positions", list(permutations(["left", "centre", "right"])))
@pytest.mark.parametrize("omitted", [0, 1, 2])
def test_unique_remaining_slot_dispatches_without_question(positions: tuple[str, ...], omitted: int) -> None:
    async def run() -> None:
        workflow, store, res, agent = composition()
        session = store.create()
        pairs = list(zip(["elephant", "bear", "hippo"], positions))
        text = ", ".join(f"{animal} {position}" for index, (animal, position) in enumerate(pairs) if index != omitted)
        await workflow.submit(session, text, "request-1")
        assert len(res.commands) == 1
        expected = dict(zip(["E", "B", "H"], ["front_" + p.replace("centre", "center") for p in positions]))
        assert res.commands[0]["target_positions"] == expected
        assert not session.awaiting_reply
        assert len(agent.contexts) == 1
    asyncio.run(run())


def test_unique_remaining_slot_after_reply() -> None:
    async def run() -> None:
        workflow, store, res, _ = composition()
        session = store.create()
        await workflow.submit(session, "elephant left", "request-1")
        assert session.awaiting_reply and not res.commands
        await workflow.answer(session, "request-1", 1, "bear centre")
        assert len(res.commands) == 1
        assert res.commands[0]["target_positions"] == TARGET
        assert not session.awaiting_reply
    asyncio.run(run())


@pytest.mark.parametrize("text", [
    "elephant left", "elephant left, bear left",
    "elephant left, elephant right, bear centre",
    "elephant left, bear centre, hippo not right",
    "elephant left, bear centre, tiger right",
    "elephant left, bear centre, it right",
])
def test_remaining_slot_does_not_override_unresolved_input(text: str) -> None:
    async def run() -> None:
        workflow, store, res, _ = composition()
        session = store.create()
        await workflow.submit(session, text, "request-1")
        assert session.awaiting_reply and not res.commands
    asyncio.run(run())


def test_contradiction_needs_explicit_correction_before_inference() -> None:
    async def run() -> None:
        workflow, store, res, _ = composition()
        session = store.create()
        await workflow.submit(session, "elephant left, elephant right, bear centre", "request-1")
        assert session.awaiting_reply and not res.commands
        await workflow.answer(session, "request-1", 1, "bear centre")
        assert session.awaiting_reply and not res.commands
        await workflow.answer(session, "request-1", 2, "elephant left")
        assert len(res.commands) == 1
        assert res.commands[0]["target_positions"] == TARGET
    asyncio.run(run())


@pytest.mark.parametrize("original", ["please arrange the animals", "elephant left"])
def test_complete_reply_recovers_and_reaches_agent(original: str) -> None:
    async def run() -> None:
        workflow, store, res, agent = composition()
        session = store.create()
        await workflow.submit(session, original, "request-1")
        assert session.awaiting_reply and not res.commands
        await workflow.answer(session, "request-1", 1, "yes")
        assert session.awaiting_reply and not res.commands
        answer = "elephant left, bear center and hippo right"
        await workflow.answer(session, "request-1", 2, answer)
        assert len(res.commands) == 1
        assert res.commands[0]["target_positions"] == TARGET
        assert agent.contexts[-1].original_request == original
        assert agent.contexts[-1].replies == ("yes", answer)
    asyncio.run(run())


def test_partial_reply_cannot_discard_unsupported_history() -> None:
    async def run() -> None:
        workflow, store, res, _ = composition()
        session = store.create()
        await workflow.submit(session, "hippo not right", "request-1")
        await workflow.answer(session, "request-1", 1, "elephant left, bear centre")
        assert session.awaiting_reply and not res.commands
    asyncio.run(run())


def test_invalid_candidate_returns_original_request_and_errors_to_command_agent() -> None:
    async def run() -> None:
        workflow, store, res, command = composition()
        session = store.create()
        text = "elephant left, bear left, hippo right"
        await workflow.submit(session, text, "request-1")
        assert len(command.contexts) == 2
        assert command.contexts[-1].original_request == text
        assert command.contexts[-1].candidate is not None
        assert command.contexts[-1].candidate["target_positions"] == {**TARGET, "B": "front_left"}
        assert command.contexts[-1].assignment == {**TARGET, "B": "front_left"}
        assert command.contexts[-1].feedback
        assert session.awaiting_reply and not res.commands
        await workflow.answer(session, "request-1", 1, "bear centre")
        assert command.contexts[-1].replies == ("bear centre",)
        assert res.commands[0]["target_positions"] == TARGET
        with pytest.raises(WorkflowConflict):
            await workflow.answer(session, "request-1", 1, "bear right")
        assert len(res.commands) == 1
    asyncio.run(run())


def test_missing_information_stale_reply_and_cancellation() -> None:
    async def run() -> None:
        workflow, store, res, _ = composition()
        session = store.create()
        await workflow.submit(session, "elephant left", "request-1")
        assert session.awaiting_reply and not res.commands
        for request_id, turn in [("other", 1), ("request-1", 2)]:
            with pytest.raises(WorkflowConflict):
                await workflow.answer(session, request_id, turn, "bear centre, hippo right")
        with pytest.raises(WorkflowConflict):
            await workflow.answer(store.create(), "request-1", 1, "bear centre, hippo right")
        await workflow.cancel(session, "request-1", 1)
        with pytest.raises(WorkflowConflict):
            await workflow.answer(session, "request-1", 1, "bear centre, hippo right")
        assert session.workflow_id is None and not res.commands
    asyncio.run(run())


def test_clarification_budget_stops_without_dispatch() -> None:
    async def run() -> None:
        workflow, store, res, _ = composition()
        session = store.create()
        await workflow.submit(session, "elephant left", "request-1")
        await workflow.answer(session, "request-1", 1, "elephant left")
        await workflow.answer(session, "request-1", 2, "elephant left")
        with pytest.raises(WorkflowFailure):
            await workflow.answer(session, "request-1", 3, "elephant left")
        assert not res.commands and not session.awaiting_reply
        assert session.workflow_id is None
    asyncio.run(run())


@pytest.mark.parametrize("payload", [
    {"action": "EXECUTE", "target_positions": TARGET},
    {"action": "VALIDATE", "command": {}, "approved": True},
    {"action": "VALIDATE", "command": "not a mapping"},
])
def test_malformed_or_unauthorized_actions_cannot_dispatch(payload: Mapping[str, object]) -> None:
    class BadRobotCommandAgent:
        async def decide(self, context: AgentContext) -> Mapping[str, object]:
            return payload

    async def run() -> None:
        store = SessionStore()
        res = RecordingRES()
        workflow = UcsWorkflow(user_command_agent=ControlledUserCommandAgent(),
                               robot_command_agent=BadRobotCommandAgent(), robot_execution_station=res, sessions=store)
        session = store.create()
        with pytest.raises(WorkflowFailure, match="Nothing was sent"):
            await workflow.submit(session, "elephant left, bear centre, hippo right", "request-1")
        assert not res.commands
        assert not session.awaiting_reply
    asyncio.run(run())


def test_user_agent_cannot_bypass_required_clarification() -> None:
    class IgnoresFeedback:
        async def decide(self, context: AgentContext) -> Mapping[str, object]:
            return {"action": "ARRANGE", "assignment": {**TARGET, "B": "front_left"}}

    async def run() -> None:
        store = SessionStore()
        res = RecordingRES()
        workflow = UcsWorkflow(user_command_agent=IgnoresFeedback(), robot_command_agent=ControlledRobotCommandAgent(),
                               robot_execution_station=res, sessions=store)
        with pytest.raises(WorkflowFailure):
            await workflow.submit(store.create(), "elephant left, bear left, hippo right", "request-1")
        assert not res.commands
    asyncio.run(run())


def test_concurrent_submission_is_blocked_while_agent_is_running() -> None:
    async def run() -> None:
        entered, release = asyncio.Event(), asyncio.Event()

        class WaitingAgent(ControlledUserCommandAgent):
            async def decide(self, context: AgentContext) -> Mapping[str, object]:
                entered.set()
                await release.wait()
                return await super().decide(context)

        store = SessionStore()
        res = RecordingRES()
        workflow = UcsWorkflow(user_command_agent=WaitingAgent(), robot_command_agent=ControlledRobotCommandAgent(),
                               robot_execution_station=res, sessions=store)
        session = store.create()
        first = asyncio.create_task(workflow.submit(session, "elephant left, bear centre, hippo right", "request-1"))
        await entered.wait()
        try:
            with pytest.raises(WorkflowConflict):
                await workflow.submit(session, "elephant right, bear centre, hippo left", "request-2")
        finally:
            release.set()
            await first
        assert len(res.commands) == 1
    asyncio.run(run())


@pytest.mark.parametrize("mode", ["missing", "mismatched", "duplicate", "failed"])
def test_execution_outcomes_are_not_misreported_or_resent(mode: str) -> None:
    class ScriptedRES(RecordingRES):
        async def execute(self, command: Mapping[str, object]) -> AsyncIterator[ControllerUpdate]:
            self.commands.append(command)
            base = ControlledRobotExecutionStation(result_delay_seconds=0)
            async for update in base.execute(command):
                if update.message_type != "result":
                    yield update
                    continue
                if mode == "missing":
                    return
                payload = dict(update.payload)
                if mode == "mismatched":
                    payload["message_id"] = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
                if mode == "failed":
                    payload["execution"] = {"status": "FAILED", "error": {"code": "EXECUTION_FAILED", "message": "Simulation failed"}}
                yield ControllerUpdate("result", payload)
                if mode == "duplicate":
                    yield ControllerUpdate("result", payload)

    async def run() -> None:
        store = SessionStore()
        res = ScriptedRES()
        workflow = UcsWorkflow(user_command_agent=ControlledUserCommandAgent(), robot_command_agent=ControlledRobotCommandAgent(),
                               robot_execution_station=res, sessions=store)
        session = store.create()
        if mode == "failed":
            await workflow.submit(session, "elephant left, bear centre, hippo right", "request-1")
            assert session.active_command is None
            assert session.last_successful_arrangement is None
        else:
            with pytest.raises(WorkflowFailure, match="unknown"):
                await workflow.submit(session, "elephant left, bear centre, hippo right", "request-1")
            assert session.active_command is not None
            with pytest.raises(WorkflowConflict):
                await workflow.submit(session, "elephant left, bear centre, hippo right", "request-2")
        assert len(res.commands) == 1
    asyncio.run(run())


@pytest.mark.parametrize("failure", ["step_limit", "agent_timeout", "execution_timeout"])
def test_limits_stop_work_without_allowing_resend(failure: str) -> None:
    async def run() -> None:
        class SlowAgent(ControlledUserCommandAgent):
            async def decide(self, context: AgentContext) -> Mapping[str, object]:
                if failure == "agent_timeout":
                    await asyncio.sleep(1)
                return await super().decide(context)

        class SlowRES(RecordingRES):
            async def execute(self, command: Mapping[str, object]) -> AsyncIterator[ControllerUpdate]:
                if failure == "execution_timeout":
                    self.commands.append(command)
                    await asyncio.sleep(1)
                    return
                async for update in super().execute(command):
                    yield update

        store = SessionStore()
        res = SlowRES()
        workflow = UcsWorkflow(user_command_agent=SlowAgent(), robot_command_agent=ControlledRobotCommandAgent(),
                               robot_execution_station=res, sessions=store,
                               max_agent_steps=1 if failure == "step_limit" else 12,
                               agent_timeout=0.01 if failure == "agent_timeout" else 20,
                               execution_timeout=0.01 if failure == "execution_timeout" else 30)
        session = store.create()
        with pytest.raises(WorkflowFailure):
            await workflow.submit(session, "elephant left, bear centre, hippo right", "request-1")
        assert len(res.commands) == (1 if failure == "execution_timeout" else 0)
        assert (session.active_command is not None) == (failure == "execution_timeout")
        with pytest.raises(WorkflowConflict):
            await workflow.submit(session, "elephant left, bear centre, hippo right", "request-1")
    asyncio.run(run())


@pytest.mark.parametrize("fault", ["schema", "arrangement", "intent", "metadata"])
def test_robot_repairs_candidate_without_changing_assignment(fault: str) -> None:
    class RepairingRobot(ControlledRobotCommandAgent):
        def __init__(self) -> None:
            self.contexts: list[AgentContext] = []
            self.accepted: Mapping[str, object] = {}

        async def decide(self, context: AgentContext) -> Mapping[str, object]:
            self.contexts.append(context)
            assert context.assignment == TARGET
            assert context.command_metadata is not None
            candidate: dict[str, object] = {**context.command_metadata, "target_positions": dict(TARGET)}
            if len(self.contexts) == 1:
                if fault == "schema":
                    candidate["unexpected"] = True
                elif fault == "arrangement":
                    candidate["target_positions"] = {**TARGET, "B": "front_left"}
                elif fault == "intent":
                    candidate["target_positions"] = {**TARGET, "E": "front_right", "H": "front_left"}
                else:
                    candidate["message_id"] = "aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa"
            else:
                assert context.feedback and context.candidate is not None
                self.accepted = candidate
            return {"action": "VALIDATE", "command": candidate}

    async def run() -> None:
        store, res, user, robot = SessionStore(), RecordingRES(), RecordingCommandAgent(), RepairingRobot()
        workflow = UcsWorkflow(user_command_agent=user, robot_command_agent=robot,
                               robot_execution_station=res, sessions=store)
        session = store.create()
        await workflow.submit(session, "elephant left, bear centre, hippo right", "repair")
        assert len(robot.contexts) == 2 and len(user.contexts) == 1
        assert res.commands == [robot.accepted]
        assert not session.awaiting_reply
        assert robot.contexts[0].command_metadata == robot.contexts[1].command_metadata
    asyncio.run(run())


@pytest.mark.parametrize("budget", [0, 1, 2])
def test_exhausted_repairs_stop_without_dispatch_or_user_question(budget: int) -> None:
    class BrokenRobot:
        calls = 0

        async def decide(self, context: AgentContext) -> Mapping[str, object]:
            self.calls += 1
            return {"action": "VALIDATE", "command": {}}

    async def run() -> None:
        store, res, robot = SessionStore(), RecordingRES(), BrokenRobot()
        workflow = UcsWorkflow(user_command_agent=ControlledUserCommandAgent(), robot_command_agent=robot,
                               robot_execution_station=res, sessions=store, max_repairs=budget)
        session = store.create()
        with pytest.raises(WorkflowFailure, match="Nothing was sent"):
            await workflow.submit(session, "elephant left, bear centre, hippo right", "exhaust")
        assert robot.calls == budget + 1
        assert not res.commands and not session.awaiting_reply and session.workflow_id is None
    asyncio.run(run())


def test_robot_cannot_mutate_interpreted_intent_through_context() -> None:
    class MutatingRobot:
        async def decide(self, context: AgentContext) -> Mapping[str, object]:
            assert isinstance(context.assignment, dict)
            context.assignment.update({"E": "front_right", "H": "front_left"})
            assert context.command_metadata is not None
            return {"action": "VALIDATE", "command": {
                **context.command_metadata, "target_positions": context.assignment,
            }}

    async def run() -> None:
        store, res = SessionStore(), RecordingRES()
        workflow = UcsWorkflow(user_command_agent=ControlledUserCommandAgent(), robot_command_agent=MutatingRobot(),
                               robot_execution_station=res, sessions=store)
        with pytest.raises(WorkflowFailure):
            await workflow.submit(store.create(), "elephant left, bear centre, hippo right", "mutate")
        assert not res.commands
    asyncio.run(run())
