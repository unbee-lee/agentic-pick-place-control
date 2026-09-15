# Live Ollama development composition

Both roles call one configurable model through Ollama's native `/api/chat`
[tool-calling API](https://docs.ollama.com/capabilities/tool-calling).
The default is provisionally `ministral-3:3b`. RES is the controlled simulator;
its successful result does not establish physical execution or verification.

## Run

Install the project with `pip install -e '.[dev]'` in the project virtual environment.
Start an Ollama service with the selected model already downloaded, then run:

```bash
UCS_OLLAMA_URL=http://127.0.0.1:11434 \
.venv/bin/uvicorn 'ucs_app.live:create_live_app' --factory --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. The page labels this as local model development
with simulated RES. `/health` reports application availability; it does not probe the model.

| Environment variable | Default | Meaning |
| --- | --- | --- |
| `UCS_OLLAMA_MODEL` | `ministral-3:3b` | Shared model identifier |
| `UCS_OLLAMA_URL` | `http://127.0.0.1:11434` | Service origin without credentials or a path |
| `UCS_OLLAMA_TIMEOUT` | `60` | Total seconds per call, including queue time (maximum 300) |
| `UCS_OLLAMA_CONTEXT` | `4096` | Context token limit |
| `UCS_OLLAMA_OUTPUT` | `512` | Output token limit |

Calls use temperature 0, seed 42, native tools, and nonstreaming responses.
One inference slot serializes calls within each application process. Use one
worker for the intended single-model composition. Cancellation releases the local
slot and closes the HTTP request; it does not guarantee immediate GPU cancellation
inside Ollama. The model is kept loaded for five minutes after use.

Each call receives fresh role instructions and explicit request/reply evidence,
assignment, metadata, candidate and feedback. There is no shared conversation history.
The adapter accepts exactly one allowed native tool call, rejects prose and malformed
actions, and returns bounded errors without raw response bodies. Only the typed
clarification question is shown as model-authored browser text. Do not enable HTTP
body or model tracing when handling private requests.

Python owns schema, arrangement, assignment-equality and metadata checks. Invalid
candidate commands return to the Robot Command Agent for at most two repairs by
default; `create_app(max_repairs=...)` can configure this budget. Malformed role
actions and technical failures stop immediately. Intent clarification requires a
real reply. The exact validated command dispatches automatically. There is no
model-failure fallback to the controlled agents.

## Live smoke check

Run against the intended model service:

```bash
UCS_OLLAMA_URL=http://127.0.0.1:11434 .venv/bin/python scripts/smoke_ollama.py
```

This checks a complete request, missing-information clarification without dispatch,
and completion after a real reply. It records commands only in memory and prints
bounded pass/fail summaries. A nonzero exit is a failed smoke check. Then exercise
the same sequence in the browser to inspect the event display.

Deterministic HTTP-boundary tests run in the normal test suite and cover malformed
actions, errors, bounded repairs, exact dispatch, context isolation, queue timeout
and cancellation. Passing those tests or this small live smoke check does not
establish clarification quality or sustained reliability; that evaluation remains
issue #7. See [the integrated workflow](integrated-workflow.md) to connect these roles
to MQTT and the separate simulated RES.
