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
.venv/bin/uvicorn 'ucs_app.live:create_live_app' --factory --host 127.0.0.1 --port 8001
```

Open <http://127.0.0.1:8001>; keep the controlled desktop demo on port 8000. The page labels this as local model development
with simulated RES. `/health` reports application availability; it does not probe the model.

The in-process simulated RES waits five seconds between BUSY and completion so
the execution stage is visible during a live demo. Model response time is additional.

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

The User Command Agent receives one fixed native-tool formatting demonstration
before the final message containing the current `AgentContext`. The demonstration
is not a real user turn, a runtime clarification, or a fallback answer. Its input
and question are fixed and never use another session's data. The Robot Command
Agent's two-message request is unchanged. Consumers inspecting the request must
read current evidence from the **last** message, not a fixed index.

This addresses #12's observed response-format mismatch without relaxing the
response contract: one `ask_clarification` call accompanied by prose is still
rejected, even if its arguments are valid. Nothing strips that prose, substitutes
the demonstration question, or automatically retries. Only a newly generated,
strictly validated model question can enter the workflow. Examples may influence
model decisions, so correct syntax does not establish intent fidelity or general
reliability. Keep assessing actual questions and assignments separately.

Adapter diagnostics contain only role, elapsed time, completion/truncation flags,
content length, tool count and up to two allowlisted tool names (unknown names are
replaced by `unknown`). They never include content, reasoning, arguments, questions,
or request text. Rejections log at WARNING; accepted shapes log at INFO when
`ucs_app.ollama` logging is enabled. No raw model/body tracing is required.

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

## Focused Mac/Jetson check for #12

Inspect/reuse a healthy existing Mac `127.0.0.1:11436` SSH tunnel forwarding to the
Jetson Ollama service at `127.0.0.1:11435`. Keep UCS on the Mac. For that layout,
set `UCS_OLLAMA_URL=http://127.0.0.1:11436` when starting the live factory on 8001.
Do not start a second tunnel on the same port, install another model, or change
Jetson resource limits. Restarting the live app invalidates its in-memory browser
sessions; reload port 8001 afterward. The controlled process on 8000 need not restart.

```bash
.venv/bin/python scripts/check_live_clarification.py http://127.0.0.1:8001
```

This check creates fresh sessions for an explicit complete request, remaining-slot
inference, and `elephant left`. Only after an actual accepted clarification with
zero dispatches does it submit `bear centre, hippo right`. It checks the exact
target, one publication, correlated progress/result and `COMPLETED`/`NOT_RUN`.
Every HTTP attempt is reported; no question means the reply stage is explicitly
`NOT_RUN`, never a pass. There are no retries and a 300-second overall budget.
Question usefulness requires separate judgement; shape/count checks do not score it.
Report model-format/semantic failures separately from HTTP/connectivity, application
and execution failures using the bounded adapter metadata and workflow state.

The remaining-slot check relies on the active intent policy; the format example
does not itself introduce inference rules. Record the source/prompt configuration
with live results. Passing this focused check does not close the broader live
acceptance in #2/#4 or deployment/recovery acceptance in #6.
