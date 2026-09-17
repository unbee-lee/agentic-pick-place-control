# Recovery and evaluation — issue #6

## Rehearsal compatibility

Recovery is **opt-in**. With `UCS_STATE_PATH` and `UCS_RES_STATE_PATH` unset,
existing startup commands, controlled request inputs, model adapters, and browser
behaviour remain unchanged. This work does not add language interpretation, camera,
speech, or fine-tuning behaviour. Keep tomorrow's established desktop configuration
unless the durable configuration below has been rehearsed separately.

The controlled parser still accepts its existing explicit animal/position pairs.
The integrated factory still uses the production Ollama roles and real MQTT.
Refreshing a completed browser workflow permits the next request;
refreshing unfinished work restores its guards/question. Retaining a browser's
session cookie allows the durable server to restore that session after restart.
Closing the browser may discard its session cookie; opening a new session is not
an operator recovery mechanism for an unresolved command.

## Identity and durable boundary

- A browser-generated request UUID identifies one workflow. Reuse is rejected
  across all sessions retained by the UCS store, including after durable restart.
- Each UCA assignment handoff increments `assignment_revision` and creates fresh
  command metadata/UUID. RCA repairs keep that assignment, revision and metadata.
- A reply must match its owning browser session, workflow UUID and clarification
  turn. Cancelled, completed, stale and cross-session replies return 409.
- SQLite stores session guards, seen request IDs, clarification context, global
  step/clarification counts, events and a dispatch journal. Each journal row links
  the exact command to its request, revision and session.
- Before the execution adapter is entered, its command reservation and active
  session guard commit together with synchronous SQLite writes. Failure to reserve
  prevents dispatch and leaves the session eligible for a fresh request; the failed
  request ID remains consumed. The in-memory active guard is installed only after
  reservation succeeds. A crash after reservation is conservatively uncertain even
  if it happened before publication. There is no automatic resend.
- Terminal completion commits the result event, cleared active guard, completed
  request snapshot and settled journal row together before notifying the browser.
  A failed terminal commit leaves the active guard intact. Restart never labels
  a committed terminal result as “Nothing was sent,” including older snapshots
  with a cleared workflow ID but a stale processing flag.
- Files have one process owner, enforced by an OS lock. Use one UCS worker and one
  simulator, distinct files, a local persistent filesystem, and unchanged paths
  on restart. This is not multi-worker or distributed recovery.

The optional simulator journal reserves a UUID before validation or simulation.
Repeated UUIDs, including changed payloads, remain suppressed after restart. It
never resumes unfinished simulation or republishes old terminal results. Losing a
result therefore can leave UCS unknown. The configured command-count bound still
stops the simulator instead of evicting deduplication history.

## Restart, timeout and cancellation policy

| Interrupted phase | Restart behaviour |
| --- | --- |
| Awaiting clarification | Restore original request, replies, question/turn and counters; wait for a real reply or cancellation |
| Interpretation or command repair in flight | Stop without dispatch; retain request ID so it cannot be replayed; a fresh request may be submitted |
| Reserved/dispatched execution | Restore “Outcome unknown” and block that session; never rerun model work or resend |
| Unknown outcome | Keep blocked until a valid correlated terminal result is durably received |
| Completed/cancelled | Retain duplicate/stale guards; completed browser refresh behaviour remains unchanged |

Global model-step and clarification limits survive a restored clarification.
Repair limits remain per interpreted assignment; interrupted repairs are aborted,
not resumed with a reset budget. Generation/transport/internal failures remain
errors, never fabricated questions asking the user to repair implementation faults.
A model timeout before reservation sends nothing. An execution timeout cannot
cancel work already accepted by RES.

Cancellation is supported only while awaiting a current clarification. It is
persisted before acknowledgement and sends nothing to RES. There is no execution
cancellation or “clear unknown” endpoint. Inspect the actual executor before any
operator-authorized recovery; do not delete the database to bypass uncertainty.

## Late results and reconnect

In durable MQTT compositions, a receive-only listener uses a stable MQTT client ID
stored in SQLite, `clean_session=False`, and a QoS1 result subscription. It reconnects
without publishing any commands. Valid non-retained results for known outstanding
command UUIDs are recorded. Unknown IDs, malformed results, retained messages and
already settled duplicates do not release guards. The first valid terminal result
wins; trusted broker permissions remain necessary.

The listener uses a continuous reader; its reconciliation timer does not cancel
MQTT reads. Shutdown gives an in-flight disconnect up to the configured MQTT
operation timeout to finish, closes the connection, and awaits the reader with
the same bounded timeout. This avoids abandoning disconnect exceptions when
shutdown races broker loss. Shutdown in durable mode may therefore take longer
than an immediate task cancellation. No global exception handler suppresses errors
in the application, and no dependency version or library internals were changed.

If a result arrives while the original request is running, reconciliation waits
until that request exits. A later result clears only the owning session's active
guard and emits correlated result/state events. `COMPLETED` with `NOT_RUN` still
means simulation completion, not verified physical placement.

`/health` reports `recovery` (`disabled`/`durable`) and `result_listener`
(`disabled`/`connecting`/`ready`/`disconnected`/`stopped`/`failed`). Application health is not
proof of model/broker/RES readiness. Check the listener is ready before submitting
in a recovery rehearsal. A failed listener needs operator investigation; it does
not release unknown outcomes.

The broker can queue QoS1 results after the listener has subscribed, while UCS is
offline. Broker persistence and queue capacity govern survival of a *broker*
restart. The repository's ephemeral development broker does not guarantee that.
MQTT acknowledgement and the application database commit are not one transaction;
a crash in between can lose a result. Disk loss, broker queue loss, or an executor
crash can leave a permanent unknown outcome. This design guarantees conservative
no-resend behaviour, not exactly-once execution or guaranteed result recovery.

## Reproduce durable mode separately

Use the existing [MQTT startup](mqtt-res.md) and [model configuration](live-ollama.md).
Create a private local directory (journal files contain request text and commands):

```bash
mkdir -p runtime-state
chmod 700 runtime-state
```

Start the simulator with its own journal:

```bash
UCS_RES_STATE_PATH="$PWD/runtime-state/res.sqlite" \
  .venv/bin/python -m ucs_app.simulated_res
```

For controlled roles with real MQTT:

```bash
UCS_STATE_PATH="$PWD/runtime-state/ucs.sqlite" \
  .venv/bin/uvicorn 'ucs_app.mqtt:create_mqtt_app' --factory --host 127.0.0.1 --port 8000
```

For live roles, use `ucs_app.live:create_integrated_app` with the same UCS state
path and existing `UCS_OLLAMA_*` configuration. Configure both processes for the
same broker. The in-process controlled/live simulator factories can persist UCS
state too, but have no independent late-result source after process death.
Use MQTT plus the separate simulator for recovery acceptance.

Keep SQLite files, WAL sidecars and lock files out of Git (`runtime-state/` is
ignored). Back up while stopped or with SQLite's backup API; do not copy only the
main file while it is active. Do not share/copy a journal into two running services
or switch factories, broker identity, or serving environment on an unresolved journal.
No automatic reset, pruning, migration or operator resolution tool is included.

For a Jetson reproduction, record the model tag/digest, Ollama version/template,
context/output limits, timeout, container memory/swap limits, broker version and
persistence settings, simulator delay, source revision and dirty diff. Reuse the
approved model configuration; do not infer live acceptance from controlled tests.
This change does not alter the Jetson service or download models.

Desktop verification uses loopback HTTP. HTTPS/reverse-proxy deployment has not
been configured or tested here. Remote deployment requires a separately reviewed
HTTPS, authentication, cookie-security and broker-permission configuration; do not
present the local rehearsal as that deployment's acceptance.

## Fixed evaluation cases and proposed assessment

These categories are specified before a new performance run. Use reviewed exact
wording and labels in a separately reviewed evaluation artifact. Existing private
rehearsal inputs and experimental records are not copied into this document.

| Category | Required observation |
| --- | --- |
| Six explicit permutations | Exact intended assignment, one validated dispatch, correlated terminal result |
| Missing information and real reply | Useful question, no early dispatch, correct merge after reply |
| Ambiguous reference / unsupported animal | No invented assignment; useful clarification rather than silent substitution |
| Conflicting intent and correction | RCA feedback reaches UCA; real user revision precedes new metadata/dispatch |
| Schema, metadata, assignment-generation mistakes | Bounded RCA repair preserves intended assignment and supplied metadata |
| Exhausted repairs / malformed action | Error, no dispatch, no question blaming the user |
| Model outage/timeout | Bounded failure before dispatch; safe public error |
| Broker/RES outage, delayed/duplicate/foreign messages | Unknown guard where delivery is uncertain; no resend; only valid correlated result reconciles |
| Browser reconnect and service restart | Context/guards survive as specified; stale/cross-session actions do not execute |

Deterministic controlled tests assess application routing, exact payloads and
recovery invariants. They are **not** measurements of language-model accuracy.
For separately authorized live runs, report per-case intent fidelity, native-call
validity, clarification outcome/turn count, feedback adaptation/repair attempts,
end-to-end and per-role latency, host/container memory and all failure categories.
Distinguish automated comparisons from human/assistant judgement of question quality;
record who reviewed labels and wording. Record cold/warm state and failed runs;
do not hide retries in latency or success counts.

Thresholds, number of runs, dataset split/review and acceptance ownership still need
user/team/supervisor agreement before calling performance “accepted.” Do not claim
fine-tuning improvement, physical safety or visual verification. Speech evaluation
remains dependent on #5 and outside this change. Private evidence stays outside Git
until the user explicitly approves sharing it.

## Verification commands and remaining acceptance

```bash
.venv/bin/pytest tests/test_recovery.py -q
.venv/bin/pytest -q
.venv/bin/mypy
.venv/bin/python -m pip check
```

The recovery suite includes real process kills, browser clarification restoration,
real-broker queued results/reconnects, duplicate results and persistent simulator
IDs. In-flight model/repair interruption is tested with a consistent database image
captured at the blocked external-call boundary; it is not a live-model crash test.
Existing integration tests retain the real Ollama HTTP adapter with controlled
responses, real MQTT and the separate simulator.

Follow-up validation on 2026-09-16 after the reservation/completion and listener
lifecycle fixes: all 194 tests passed in the current workspace, including 19 recovery cases; strict mypy
passed on 33 source/test/script files; dependency and diff checks passed. Four new
recovery cases cover failed reservation/reuse and terminal snapshots before commit,
after commit, and with the older stale processing flag. The total also includes
the separate demo tasks' tests, which were not changed by this recovery fix.
The existing browser suite also passed with the default rehearsal configuration.
Existing LibreSSL/LangGraph dependency warnings remain. The aiomqtt unhandled-future
diagnostic was reproduced as a failing assertion in the broker-outage test. A
continuous reader plus bounded disconnect draining fixed the reproduced shutdown
race, including a socket callback error exposed during investigation. The expanded
19-case recovery suite passes. Three outage/shutdown timing cases capture asyncio
exception-handler reports and force garbage collection, asserting none occurred;
they also check the unknown guard and absence of a command resend after reconnect.
This is evidence for those exercised paths, not proof against every possible
network/dependency failure. No live-model performance, Jetson recovery or HTTPS
measurements were made in this change.

Remaining acceptance: live-model criteria under #2/#4, agreed evaluation thresholds
and measurements, deployed Jetson recovery rehearsal, broker-persistence/queue-loss
behaviour in the intended deployment, and HTTPS where used. Issue #6 should remain
open until the applicable acceptance evidence and assessment agreement exist.

Recovery-only checkpoint verification on 2026-09-16: an isolated snapshot excluding
the separate demo UI, image assets, inference-policy edits and expanded #4 tests
passed all 161 tests, including the 19 recovery cases. Strict mypy passed on 32
source/test/script files, and dependency and diff checks passed. The recovery
browser test checks that a completed session permits a new submission, independent
of whether the separate demo displays Ready or Completed after refresh. These
checkpoint checks do not provide live-model or deployed recovery acceptance.
