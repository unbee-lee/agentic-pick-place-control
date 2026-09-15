# MQTT and separate Simulated RES

This composition connects controlled role agents to a real MQTT broker and a
separate simulator process. It needs no Ollama service. Both UCS and RES validate
commands independently. Simulated success means execution `COMPLETED` and
verification `NOT_RUN`; no physical movement or visual verification is performed.

## Start locally

Install Mosquitto so `mosquitto` is on PATH (for example, `brew install mosquitto`
on macOS or `sudo apt install mosquitto` on Debian/Ubuntu). From this repository,
install the project and browser test dependencies:

```bash
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/python -m playwright install chromium
```

Run each command in a separate terminal from the repository root:

```bash
# Terminal 1: foreground broker, loopback only, no persistent messages.
mosquitto -c config/mosquitto-local.conf

# Terminal 2: wait for "Simulated RES ready" before submitting requests.
.venv/bin/python -m ucs_app.simulated_res

# Terminal 3: UCS with controlled agents and MQTT execution adapter.
.venv/bin/uvicorn 'ucs_app.mqtt:create_mqtt_app' --factory --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. If port 1883 is occupied, use an unused port in a
copy of the broker config and set `UCS_MQTT_PORT` in both UCS and RES terminals.
Use a dedicated broker for this composition and one simulator for its `robot/*`
namespace. `/health` reports UCS availability, not broker or simulator readiness.

## Configuration

Environment variables are read by the server processes; `.env` is not loaded
automatically. Broker credentials are never sent to the browser.

| Variable | Default | Meaning |
| --- | --- | --- |
| `UCS_MQTT_HOST` | `127.0.0.1` | Broker hostname |
| `UCS_MQTT_PORT` | `1883` | Broker port |
| `UCS_MQTT_USERNAME` | unset | Optional broker username |
| `UCS_MQTT_PASSWORD` | unset | Optional broker password, masked in configuration representations |
| `UCS_MQTT_TLS_CA` | unset | CA file; enables TLS with certificate and hostname verification |
| `UCS_MQTT_OPERATION_TIMEOUT` | `5` | MQTT acknowledgement timeout in seconds |
| `UCS_MQTT_EXECUTION_TIMEOUT` | `30` | UCS total execution deadline in seconds, including connection and publication |
| `UCS_RES_DELAY` | `0.75` | Seconds between simulated BUSY and DONE |

The supplied anonymous broker config is bound to loopback. For a remote broker,
configure its authentication/ACLs and TLS separately, then supply credentials
and a CA file to both server processes. Never commit credentials.

## Delivery and failure behaviour

- UCS subscribes to `robot/status` and `robot/result` before publishing the exact
  validated JSON object to `robot/command`.
- All publications use QoS 1 and `retain=False`. MQTT can deliver duplicates;
  this is not a claim of exactly-once transport.
- UCS discards retained replies and replies for other message IDs. It validates
  matching BUSY/DONE payloads before exposing progress or accepting completion.
  Malformed incoming JSON stops the active execution conservatively.
- RES validates the schema and unique-animal/unique-position invariant before
  simulation. Invalid commands with usable UUIDs return `NOT_STARTED` /
  `INVALID_COMMAND`, with no BUSY update. Unparseable or uncorrelatable commands
  are discarded. Retained command replays at subscription are ignored.
- RES reserves every accepted UUID before validation or work, suppresses all
  later copies of that UUID (including changed payloads), and executes sequentially.
  It stops at 4096 distinct UUIDs instead of evicting history and permitting replay.
- Each UCS execution has one connection and one application-level publication;
  it does not reconnect or resubmit automatically. Timeout, malformed matching
  results, and connection loss preserve the session's active/unknown guard.
  Refreshing the browser cannot clear that guard. Even a connection failure
  before publication is conservatively displayed as unknown by the current workflow.
- A simulator outage can leave a published command without a result. Timeout
  does not cancel any execution already accepted by RES. A late result does not
  automatically reconcile an unknown outcome in this implementation.

The implementation uses [aiomqtt's managed connection and subscription APIs](https://aiomqtt.bo3hm.com/subscribing-to-a-topic).
UCS sessions and RES duplicate tracking are in memory. Restart recovery, durable
tracking and late-result reconciliation remain issue #6. Do not restart processes
or open a new session as a way to clear an uncertain execution; inspect the broker
and simulator state first. Issue #4 combines this transport with live model roles.

## Reproducible smoke checks

With the three foreground processes running:

1. Submit `elephant left, bear centre, hippo right`. Expect one publication,
   correlated progress, Completed, and the statement that physical placement
   was not verified. There is no target confirmation step.
2. Submit `elephant left`. Expect clarification and no publication. Reply
   `bear centre, hippo right`; expect one completed simulated execution.
3. Stop RES with Ctrl-C, then submit another complete arrangement. After the
   configured deadline, expect Outcome unknown and submission disabled. Refresh
   the page; that guard must remain. Do not resend the uncertain command.

For automated checks (no foreground services needed):

```bash
.venv/bin/pytest tests/test_mqtt.py -q
.venv/bin/pytest
.venv/bin/mypy
.venv/bin/python -m pip check
```

Tests launch isolated Mosquitto instances, separate simulator/UCS processes where
needed, and headless Chromium on temporary local ports. They cover all six browser
arrangements, clarification, exact wire payloads, independent RES rejection,
duplicates, retained messages, correlation, concurrent requests, broker loss,
missing simulator and unknown-outcome refresh. Missing Mosquitto is a test failure,
not a silent skip. The fixtures terminate only the processes they start.

For manual cleanup, stop UCS, RES and the foreground broker with Ctrl-C in their
own terminals. The supplied broker config disables persistence; starting it again
does not restore retained messages or sessions. Keep it separate from other work.
