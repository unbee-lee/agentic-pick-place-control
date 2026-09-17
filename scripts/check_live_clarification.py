"""Bounded HTTP/SSE live checks. No retries, raw model output, or fabricated replies."""

import asyncio
import json
import sys
import time
from typing import Any
from uuid import uuid4

import httpx


async def snapshot(client: httpx.AsyncClient) -> dict[str, Any]:
    async with client.stream("GET", "/events", timeout=10) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                value: dict[str, Any] = json.loads(line[6:])
                return value
    raise RuntimeError("No SSE snapshot")


def outcome(state: dict[str, Any], target: dict[str, str]) -> dict[str, object]:
    events = state["events"]
    pubs = [e for e in events if e["kind"] == "command_publication"]
    targets = [e for e in events if e["kind"] == "proposal"]
    results = [e for e in events if e["kind"] == "result"]
    progress = [e for e in events if e["kind"] == "progress"]
    exact = len(targets) == 1 and targets[0]["details"].get("target_positions") == target
    completed = (len(results) == 1 and results[0]["details"].get("execution_status") == "COMPLETED"
                 and results[0]["details"].get("verification_status") == "NOT_RUN")
    correlated = (len(pubs) == 1 and bool(progress) and len(results) == 1
                  and all(e["details"].get("message_id") == pubs[0]["details"]["message_id"]
                          for e in [*progress, *results]))
    return {"dispatches": len(pubs), "exact_target": exact, "completed_not_verified": completed,
            "correlated": correlated, "passed": bool(exact and completed and correlated
                and not state["active"] and not state["awaiting_reply"])}


async def attempt(client: httpx.AsyncClient, label: str, path: str, body: dict[str, object],
                  target: dict[str, str], *, clarification: bool = False) -> tuple[bool, dict[str, Any]]:
    start = time.monotonic()
    print(json.dumps({"attempt": label, "status": "STARTED"}), flush=True)
    try:
        response = await client.post(path, json=body)
        state = await snapshot(client)
        result: dict[str, object] = {"attempt": label, "http_status": response.status_code,
                                    "seconds": round(time.monotonic() - start, 3)}
        if clarification:
            questions = [e for e in state["events"] if e["kind"] == "clarification"]
            dispatches = sum(e["kind"] == "command_publication" for e in state["events"])
            passed = response.status_code == 202 and state["awaiting_reply"] and bool(questions) and dispatches == 0
            result.update(passed=bool(passed), questions=len(questions), dispatches=dispatches)
            # Question wording is judged in the browser, not automatically scored here.
        else:
            result.update(outcome(state, target))
            passed = response.status_code == 202 and result["passed"] is True
            result["passed"] = bool(passed)
        if not passed:
            result["failure_stage"] = ("execution" if state["active"] else
                                       "application_or_model: inspect bounded server metadata")
        print(json.dumps(result), flush=True)
        return bool(passed), state
    except (httpx.HTTPError, RuntimeError, KeyError, ValueError) as error:
        print(json.dumps({"attempt": label, "passed": False,
                          "failure_stage": "HTTP/SSE connectivity or application",
                          "error_type": type(error).__name__, "seconds": round(time.monotonic() - start, 3)}), flush=True)
        return False, {}


async def check(base_url: str) -> bool:
    passed = True
    standard = {"E": "front_left", "B": "front_center", "H": "front_right"}
    cases = [
        ("explicit_complete", "elephant left, bear centre, hippo right", standard),
        ("remaining_slot", "elephant right, bear left", {"E": "front_right", "B": "front_left", "H": "front_center"}),
        ("clarification", "elephant left", standard),
    ]
    for label, text, target in cases:
        async with httpx.AsyncClient(base_url=base_url, timeout=150, trust_env=False) as client:
            try:
                response = await client.get("/")
                response.raise_for_status()
            except httpx.HTTPError as error:
                print(json.dumps({"attempt": label, "passed": False, "failure_stage": "application_connectivity",
                                  "error_type": type(error).__name__}), flush=True)
                if label == "clarification":
                    print(json.dumps({"attempt": "actual_reply", "status": "NOT_RUN",
                                      "reason": "Application unavailable; no question produced"}), flush=True)
                passed = False
                continue
            ok, state = await attempt(client, label, "/arrangement-requests",
                                      {"text": text, "request_id": str(uuid4())}, target,
                                      clarification=label == "clarification")
            passed = passed and ok
            if label == "clarification":
                if not ok:
                    print(json.dumps({"attempt": "actual_reply", "status": "NOT_RUN",
                                      "reason": "No accepted clarification; no reply-path pass claimed"}), flush=True)
                    continue
                ok, _ = await attempt(client, "actual_reply", "/clarifications",
                                      {"text": "bear centre, hippo right", "workflow_id": state["workflow_id"],
                                       "turn": state["turn"]}, target)
                passed = passed and ok
    return passed


if __name__ == "__main__":
    try:
        ok = asyncio.run(asyncio.wait_for(check(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8001"), 300))
    except asyncio.TimeoutError:
        print('{"passed":false,"failure_stage":"overall 300-second budget exhausted"}', flush=True)
        raise SystemExit(1) from None
    raise SystemExit(0 if ok else 1)
