"""Synthetic scripted decisions, never live-model or held-out acceptance."""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from test_ollama import RecordingRES, response
from ucs_app.controlled import ControlledUserCommandAgent
from ucs_app.interfaces import AgentContext
from ucs_app.ollama import (
    USER_INSTRUCTIONS, OllamaClient, OllamaConfig,
    OllamaRobotCommandAgent, OllamaUserCommandAgent,
)
from ucs_app.sessions import SessionStore
from ucs_app.workflow import UcsWorkflow

UNIQUE = "Reserve the right slot for bear and the left slot for elephant. Keep hippo away from the left slot."
TARGET = {"E": "front_left", "B": "front_right", "H": "front_center"}


@pytest.mark.parametrize("request_text,replies,question", [
    (UNIQUE, (), None),
    ("Reserve the right slot for bear and the left slot for elephant. Keep hippo away from the middle slot.", (),
     "Hippo would have to occupy the middle, which you prohibited. Which instruction should change?"),
    ("Keep bear away from the right slot; no other positions are specified.", (),
     "Which positions should elephant and hippo occupy?"),
    ("Elephant left and bear right; hippo cannot be left. Give the otter a slot too.", (),
     "Otter is unsupported. Which supported animals and positions do you intend?"),
    ("Elephant left and elephant middle; bear right; hippo cannot be right.", (),
     "Elephant has conflicting destinations. Which elephant instruction should change?"),
    ("Bear cannot be right.", ("Move it into the middle instead.",),
     "Which animal does it refer to, and what are the other destinations?"),
    ("Elephant left and bear right; hippo cannot be left. Put hippo behind the others.", (),
     "Behind is unsupported. Which front position should hippo occupy?"),
])
def test_scripted_negative_policy_workflow(
    request_text: str, replies: tuple[str, ...], question: str | None,
) -> None:
    async def run() -> None:
        seen: list[dict[str, object]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            context = json.loads(payload["messages"][-1]["content"])
            seen.append(context)
            if payload["messages"][0]["content"] == USER_INSTRUCTIONS:
                if replies and not context["replies"]:
                    return httpx.Response(200, json=response("ask_clarification", {
                        "question": "Please specify the destinations."}))
                if question is not None:
                    return httpx.Response(200, json=response("ask_clarification", {"question": question}))
                return httpx.Response(200, json=response("handoff_assignment", {"assignment": TARGET}))
            assert context["assignment"] == TARGET
            return httpx.Response(200, json=response("validate_command", {"command": {
                **context["command_metadata"], "target_positions": context["assignment"],
            }}))

        client = OllamaClient(OllamaConfig(), transport=httpx.MockTransport(handler))
        store, res = SessionStore(), RecordingRES()
        workflow = UcsWorkflow(user_command_agent=OllamaUserCommandAgent(client),
                               robot_command_agent=OllamaRobotCommandAgent(client),
                               robot_execution_station=res, sessions=store)
        session = store.create()
        await workflow.submit(session, request_text, "synthetic-policy")
        for reply in replies:
            assert not res.commands
            await workflow.answer(session, "synthetic-policy", session.clarification_turn, reply)
        assert seen[-1]["original_request"] == request_text
        assert seen[-1]["replies"] == list(replies)
        if question is None:
            assert not session.awaiting_reply
            assert len(res.commands) == 1
            assert res.commands[0]["target_positions"] == TARGET
            assert len(seen) == 2
        else:
            assert session.awaiting_reply and not res.commands
            assert len(seen) == 1 + len(replies)
            assert [event.message for event in session._events if event.kind == "clarification"][-1] == question
    asyncio.run(run())


def test_prompt_contains_amended_decision_boundaries() -> None:
    for clause in (
        "Negative instructions are valid arrangement constraints",
        "exactly one valid complete arrangement",
        "If no valid arrangement remains",
        "If multiple valid arrangements remain",
        "Never ignore a negative constraint",
        "ambiguous corrections require clarification",
    ):
        assert clause in USER_INSTRUCTIONS
    assert "ambiguous pronouns or negative instructions" not in USER_INSTRUCTIONS


def test_controlled_negative_language_remains_outside_grammar() -> None:
    action = asyncio.run(ControlledUserCommandAgent().decide(AgentContext(UNIQUE)))
    assert action["action"] == "CLARIFY"
    assert "explicit pairs only" in str(action["question"])
