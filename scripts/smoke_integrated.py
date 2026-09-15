"""Opt-in live HTTP smoke check; uses real model, broker and separate RES services."""

import asyncio
from itertools import permutations
import json
import sys
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
    raise RuntimeError("No browser snapshot received")


def completed(state: dict[str, Any], target: dict[str, str]) -> bool:
    events = state["events"]
    publications = [e for e in events if e["kind"] == "command_publication"]
    proposals = [e for e in events if e["kind"] == "proposal"]
    results = [e for e in events if e["kind"] == "result"]
    return (len(publications) == len(proposals) == len(results) == 1
            and proposals[0]["details"]["target_positions"] == target
            and results[0]["details"]["execution_status"] == "COMPLETED"
            and results[0]["details"]["verification_status"] == "NOT_RUN"
            and not state["active"] and not state["awaiting_reply"])


async def check(base_url: str) -> bool:
    passed = True
    for index, positions in enumerate(permutations(["left", "centre", "right"])):
        async with httpx.AsyncClient(base_url=base_url, timeout=300, trust_env=False) as client:
            await client.get("/")
            text = ", ".join(f"{a} {p}" for a, p in zip(["elephant", "bear", "hippo"], positions))
            target = dict(zip(["E", "B", "H"], ["front_" + p.replace("centre", "center") for p in positions]))
            response = await client.post("/arrangement-requests", json={"text": text, "request_id": str(uuid4())})
            ok = response.status_code == 202 and completed(await snapshot(client), target)
            print(f"{'PASS' if ok else 'FAIL'} arrangement {index + 1}", flush=True)
            passed = passed and ok
    async with httpx.AsyncClient(base_url=base_url, timeout=300, trust_env=False) as client:
        await client.get("/")
        response = await client.post("/arrangement-requests", json={"text": "elephant left", "request_id": str(uuid4())})
        state = await snapshot(client)
        waits = (response.status_code == 202 and state["awaiting_reply"]
                 and not any(e["kind"] == "command_publication" for e in state["events"]))
        print(f"{'PASS' if waits else 'FAIL'} clarification waits without dispatch", flush=True)
        ok = False
        if waits:
            response = await client.post("/clarifications", json={"text": "bear centre, hippo right",
                                         "workflow_id": state["workflow_id"], "turn": state["turn"]})
            ok = response.status_code == 202 and completed(await snapshot(client), {
                "E": "front_left", "B": "front_center", "H": "front_right"})
        print(f"{'PASS' if ok else 'FAIL'} actual clarification reply completes", flush=True)
        passed = passed and waits and ok
    return passed


if __name__ == "__main__":
    try:
        ok = asyncio.run(check(sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"))
    except (httpx.HTTPError, RuntimeError, KeyError, ValueError):
        print("FAIL smoke transport/state error; check UCS, model, broker and simulator services")
        raise SystemExit(1) from None
    raise SystemExit(0 if ok else 1)
