# Integrated typed workflow

The integrated factory connects browser/FastAPI/LangGraph to both live Ollama roles,
real MQTT and a separate simulated RES. Interpretation and clarification remain
model responsibilities. Python retains validation, bounded repairs, session guards
and dispatch. No extraction-only prototype or deterministic model fallback is used.

## Start

Install dependencies and start Mosquitto and the separate RES using
[the MQTT instructions](mqtt-res.md). Start Ollama with the configured model already
downloaded, using [the model configuration](live-ollama.md). Then start UCS:

```bash
UCS_OLLAMA_URL=http://127.0.0.1:11434 \
UCS_MQTT_HOST=127.0.0.1 UCS_MQTT_PORT=1883 \
.venv/bin/uvicorn 'ucs_app.live:create_integrated_app' --factory --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. Use one UCS worker and one simulator on the dedicated
broker. The default model remains provisionally `ministral-3:3b`. Credentials and
model/broker configuration stay server-side. The simulator must use the same broker
configuration as UCS. Set `UCS_RES_DELAY` lower than `UCS_MQTT_EXECUTION_TIMEOUT` with
room for transport overhead. All `UCS_OLLAMA_*` and `UCS_MQTT_*` settings documented
in the linked guides apply; there are no new environment variables for this factory.

The original factories remain useful for isolating dependencies:

| Factory | Role adapters | Execution |
| --- | --- | --- |
| `ucs_app.controlled:create_controlled_app` | Controlled | In-process simulation |
| `ucs_app.mqtt:create_mqtt_app` | Controlled | MQTT + separate RES |
| `ucs_app.live:create_live_app` | Live Ollama | In-process simulation |
| `ucs_app.live:create_integrated_app` | Live Ollama | MQTT + separate RES |

## Health and failures

`GET /health` checks only the UCS application. It does not establish dependency
readiness. Check the model service's `/api/tags` for the configured model and wait
for `Simulated RES ready` before submitting a request. Successful subscription
acknowledgements are checked before command publication.

Model failures provide a fixed hint to inspect the Ollama service/configuration.
Transport failures identify broker connectivity, permissions and the simulated RES.
Raw exception bodies and model output are not shown. An unknown execution outcome
must be established before retrying; there is no automatic resend, and refreshing
cannot clear the session guard. A timeout does not cancel work already accepted by
RES. Startup configuration errors require correcting the server environment.

## Deterministic integrated checks

```bash
.venv/bin/pytest tests/test_integrated.py -q
.venv/bin/pytest
.venv/bin/mypy
.venv/bin/python -m pip check
```

These tests start a scripted Ollama HTTP server, isolated real Mosquitto, separate
RES and UCS processes, and Chromium. Requests still traverse the production Ollama
and MQTT adapters. The fake model exists only in tests and is never selected by
application failure handling. Tests inspect the MQTT wire payload to verify that the
exact validated candidate is sent once.

Coverage includes all six arrangements, actual clarification replies, explicit
assignment/metadata handoff, assignment-preserving repair, exhausted repairs,
malformed model actions, service unavailability and model timeout. Existing MQTT
checks cover broker loss and missing simulator. No Confirm endpoint or target
approval step is introduced.

## Live smoke test

With all four real services running, use:

```bash
.venv/bin/python scripts/smoke_integrated.py http://127.0.0.1:8000
```

The script creates isolated browser-cookie sessions through the HTTP interface,
checks all six arrangements, then checks no dispatch before a clarification reply
and completion afterward. It reads the same SSE snapshot as the browser, verifies
the expected target and `COMPLETED`/`NOT_RUN`, and prints bounded stage results.
A nonzero exit means the smoke check failed. It does not retry failed requests.
It does not establish clarification-question quality or sustained model reliability.
Deterministic tests and the real-model smoke result must be reported separately.

This composition makes the infrastructure testable while #2's live clarification
acceptance and #7's broader reliability evaluation remain open. Issue #4 is not
complete merely because deterministic tests pass. Fine-tuning, speech, physical
hardware and visual verification are outside this change.

Stop UCS, simulator, and the foreground development broker with Ctrl-C in their
own terminals. Follow the linked MQTT guide for unknown-outcome and restart limits;
durable recovery remains #6. Tests clean up only their own child processes.
