"""Opt-in smoke check using live agents and a recording simulated RES."""

import asyncio
from typing import AsyncIterator, Mapping
from uuid import uuid4

from ucs_app.controlled import ControlledRobotExecutionStation
from ucs_app.interfaces import ControllerUpdate
from ucs_app.ollama import OllamaClient, OllamaConfig, OllamaUserCommandAgent, OllamaRobotCommandAgent
from ucs_app.sessions import SessionStore
from ucs_app.workflow import UcsWorkflow, WorkflowFailure


class RecordingRES(ControlledRobotExecutionStation):
    def __init__(self) -> None:
        super().__init__(result_delay_seconds=0)
        self.commands: list[Mapping[str, object]] = []

    async def execute(self, command: Mapping[str, object]) -> AsyncIterator[ControllerUpdate]:
        self.commands.append(command)
        async for update in super().execute(command):
            yield update


async def main() -> None:
    config = OllamaConfig.from_env()
    client = OllamaClient(config)
    store, res = SessionStore(), RecordingRES()
    workflow = UcsWorkflow(user_command_agent=OllamaUserCommandAgent(client),
                           robot_command_agent=OllamaRobotCommandAgent(client),
                           robot_execution_station=res, sessions=store,
                           agent_timeout=config.timeout_seconds + 1)
    complete = store.create()
    await workflow.submit(complete, "elephant left, bear centre, hippo right", str(uuid4()))
    assert len(res.commands) == 1, "Complete request did not dispatch exactly once"
    assert res.commands[0]["target_positions"] == {
        "E": "front_left", "B": "front_center", "H": "front_right"}, "Intent mismatch"
    print("PASS complete request: one exact validated simulated dispatch", flush=True)
    missing = store.create()
    request_id = str(uuid4())
    await workflow.submit(missing, "elephant left", request_id)
    assert missing.awaiting_reply and len(res.commands) == 1, "Missing request did not wait"
    print("PASS missing information: clarification with no dispatch", flush=True)
    await workflow.answer(missing, request_id, missing.clarification_turn, "bear centre, hippo right")
    assert len(res.commands) == 2, "Clarification reply did not complete"
    assert res.commands[-1]["target_positions"] == res.commands[0]["target_positions"], "Reply intent mismatch"
    print("PASS actual reply: one exact validated simulated dispatch", flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (WorkflowFailure, AssertionError) as error:
        # WorkflowFailure carries only bounded public diagnostics.
        print(f"FAIL {error}")
        raise SystemExit(1) from None
