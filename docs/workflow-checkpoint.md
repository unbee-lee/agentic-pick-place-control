# End-to-end workflow checkpoint

This checkpoint records an implemented user-to-simulation workflow, before Jetson
application deployment and full live-model acceptance. It is not a production
readiness or model-accuracy claim.

## Implemented path

Typed input or reviewed speech transcript → User Command Agent → actual user
clarification when needed → Robot Command Agent → Python validation and bounded
repair → MQTT → separate simulated RES → correlated browser progress and result.

`ucs_app.live:create_integrated_app` selects this path. It requires Ollama, a real
broker, and the separate simulator. `create_live_app` instead uses an in-process
simulated RES; the controlled factory also substitutes scripted role decisions.
All factories share the browser and workflow. There is no live-model fallback to
controlled answers. Speech supplies editable text and never submits automatically.

The gate checks schema, distinct supported positions, assignment equality and
application metadata. Repairs preserve intent and metadata; invalid or exhausted
work is not dispatched. Durable state and receive-only late-result reconciliation
are opt-in; see [recovery](recovery-and-evaluation.md).

## Controlled rehearsal and optional images

The controlled role supports explicit animal-position pairs, unique remaining-slot
inference, contradiction correction, and complete valid restatements after unsupported
input. This is a limited parser, not general language understanding. Clarification
answers are shown beneath the original request and restored for unfinished sessions.

The in-process simulator waits five seconds; actual model-call latency is additional.
Optional `before.jpg` and `after.jpg` files are read from the server's working
directory. They are local-only, ignored by Git, and not included in this checkpoint.
Missing files produce unavailable placeholders. The prepared after photo is configured
only for elephant left, bear centre, hippo right and is shown only after correlated
completion. Other arrangements remain executable but have no matching photo.
These are illustrative images, not camera observations; verification remains NOT_RUN.

## Acceptance still outstanding

- Live-model interpretation, useful clarification/replies, remaining-slot inference,
  and assignment-preserving repair under #2, #4, #7 and #12.
- Jetson dependency/setup verification and integrated live execution with measured
  latency and resource use. No model or container limits are changed here.
- Deployed durable-recovery rehearsal, broker persistence/queue-loss behaviour, and
  agreed evaluation criteria under #6.
- Deployed speech/microphone checks and secure browser access under #5. The current
  compact UI lacks the visible Google-processing disclosure requested by that issue.
- Fine-tuning and equivalent before/after evaluation under #11, conditional on
  training hardware, reviewed data, and deployment compatibility.

Physical robot control and automated visual verification remain outside this scope.
Keep related issues open until their own acceptance criteria are satisfied.

Run the full test suite, strict mypy, dependency checks and diff checks for the
reviewed revision. Deterministic tests exercise known model responses and simulated
execution; report real-model evidence separately. Private notes, datasets, raw model
outputs, recordings and runtime journals must not be added to the PR.
