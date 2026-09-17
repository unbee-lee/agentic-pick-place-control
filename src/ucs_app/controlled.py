"""Explicitly controlled development adapters for the UCS."""

import asyncio
import re
from typing import AsyncIterator, Mapping

from fastapi import FastAPI

from ucs_app.app import create_app
from ucs_app.interfaces import AgentContext, ControllerUpdate
from ucs_app.transport import utc_timestamp


class ControlledUserCommandAgent:
    """Scripted role for the explicit-position demo, not an LLM."""

    async def decide(self, context: AgentContext) -> Mapping[str, object]:
        if context.feedback:
            return {"action": "CLARIFY", "question": context.feedback}
        pair_pattern = r"(elephant|bear|hippo)\s+(left|centre|center|right)\b"

        def explicit_pairs_only(text: str) -> bool:
            remaining = re.sub(pair_pattern, "", text.lower())
            remaining = re.sub(r"\band\b|[\s,.;]", "", remaining)
            return not remaining

        texts = (context.original_request, *context.replies)
        # A complete, unambiguous actual reply can restate the whole target.
        # Keep the full evidence in AgentContext; only the scripted parser resets.
        for index in range(len(texts) - 1, 0, -1):
            text = texts[index]
            pairs = {(animal, position.replace("centre", "center"))
                     for animal, position in re.findall(pair_pattern, text.lower())}
            if (explicit_pairs_only(text) and len(pairs) == 3
                    and len({animal for animal, _ in pairs}) == 3
                    and len({position for _, position in pairs}) == 3):
                texts = texts[index:]
                break
        for text in texts:
            if not explicit_pairs_only(text):
                return {"action": "CLARIFY", "question": (
                    "This controlled demo accepts explicit pairs only. Please reply with "
                    "all three positions, for example: elephant left, bear centre, hippo right."
                )}
        target: dict[str, str] = {}
        contradictions: set[str] = set()
        for text in texts:
            turn_positions: dict[str, set[str]] = {}
            for animal, position in re.findall(
                r"(elephant|bear|hippo)\s+(left|centre|center|right)\b", text.lower()
            ):
                key = {"elephant": "E", "bear": "B", "hippo": "H"}[animal]
                destination = "front_" + position.replace("centre", "center")
                turn_positions.setdefault(key, set()).add(destination)
                target[key] = destination
            for key, destinations in turn_positions.items():
                if len(destinations) > 1:
                    contradictions.add(key)
                else:
                    # An explicit later reply may correct an earlier contradiction.
                    contradictions.discard(key)
        if contradictions:
            return {"action": "CLARIFY", "question": (
                "An animal has conflicting destinations. Please specify one position "
                "for each animal with conflicting destinations."
            )}
        if len(target) == 2 and len(set(target.values())) == 2:
            missing_animal = ({"E", "B", "H"} - target.keys()).pop()
            remaining_slot = ({"front_left", "front_center", "front_right"} - set(target.values())).pop()
            target[missing_animal] = remaining_slot
        if len(target) != 3:
            return {"action": "CLARIFY", "question": (
                "Please specify the missing positions using animal and position pairs, "
                "for example: elephant left, bear centre, hippo right."
            )}
        return {"action": "ARRANGE", "assignment": target}


class ControlledRobotCommandAgent:
    """Serialize explicit intent; request clarification for conflicting intent."""

    async def decide(self, context: AgentContext) -> Mapping[str, object]:
        if context.assignment is None or context.command_metadata is None:
            raise RuntimeError("missing interpreted assignment or command metadata")
        if context.feedback and len(set(context.assignment.values())) != 3:
            return {"action": "ASK_USER", "question": (
                "Each animal needs a different front position. Please revise the conflicting positions."
            )}
        return {"action": "VALIDATE", "command": {
            **context.command_metadata, "target_positions": dict(context.assignment),
        }}


class ControlledRobotExecutionStation:
    """Emit deterministic correlated progress and simulated success."""

    def __init__(self, *, result_delay_seconds: float) -> None:
        if result_delay_seconds < 0:
            raise ValueError("result delay cannot be negative")
        self._result_delay_seconds = result_delay_seconds

    async def execute(
        self,
        command: Mapping[str, object],
    ) -> AsyncIterator[ControllerUpdate]:
        message_id = command.get("message_id")
        if not isinstance(message_id, str):
            raise RuntimeError("controlled RES received no message identifier")

        yield ControllerUpdate(
            message_type="status",
            payload={
                "schema_version": "1.0",
                "message_id": message_id,
                "status": "BUSY",
                "stage": "EXECUTING_MOVES",
                "timestamp": utc_timestamp(),
            },
        )
        await asyncio.sleep(self._result_delay_seconds)
        yield ControllerUpdate(
            message_type="result",
            payload={
                "schema_version": "1.0",
                "message_id": message_id,
                "status": "DONE",
                "execution": {"status": "COMPLETED", "error": None},
                "verification": {
                    "status": "NOT_RUN",
                    "outcome": None,
                    "observed_positions": None,
                    "error": None,
                },
                "completed_at": utc_timestamp(),
            },
        )


def create_controlled_app(*, result_delay_seconds: float = 5.0) -> FastAPI:
    """Build the explicitly labelled controlled-development composition."""

    return create_app(
        user_command_agent=ControlledUserCommandAgent(),
        robot_command_agent=ControlledRobotCommandAgent(),
        robot_execution_station=ControlledRobotExecutionStation(
            result_delay_seconds=result_delay_seconds
        ),
        composition_label="Controlled development",
    )
