# Project handover

Last reviewed: 22 October 2026

## Purpose and scope

This repository contains a browser-based User Command Station (UCS) that uses two
sequential local-LLM roles to interpret a requested arrangement, prepare a robot
command, validate it in Python, and send it over MQTT to a separate Simulated
Robot Execution Station (RES).

The assessed baseline ends at simulated execution. Physical robot control, ROS,
camera perception and visual placement verification are not implemented by this
repository. A simulated result of `COMPLETED` / `NOT_RUN` means that the simulator
completed the command; it is not evidence that a physical arrangement was made or
visually verified.

## Start here

1. Read this file and [the system design](docs/design.md).
2. For the deterministic development harness, follow
   [the controlled composition](docs/controlled-ucs.md).
3. For the integrated model/MQTT path, follow
   [the integrated workflow](docs/integrated-workflow.md).
4. For the existing Jetson installation, preserve its current private paths and
   durable state and follow [the Jetson deployment guide](docs/jetson-deployment.md).
5. Check the open GitHub issues before claiming acceptance or changing the model,
   deployment revision or recovery state.

## Current implementation

| Capability | State | Evidence boundary |
| --- | --- | --- |
| Browser request and clarification UI | Implemented | Deterministic browser coverage; live wording quality remains model-dependent |
| User Command Agent and Robot Command Agent | Implemented with one configurable Ollama model | Strict native-tool decoding and bounded calls; broad live-model reliability is not accepted |
| Python validation and bounded repair | Implemented | Schema, arrangement, assignment and metadata checks; not proof of correct user-intent interpretation |
| Real MQTT and separate Simulated RES | Implemented and merged | Exact command correlation and simulated progress/result checks |
| Reviewed speech transcript flow | Implemented | Recognition cannot submit automatically; real microphone/Google deployment acceptance remains open |
| Durable UCS/RES state and late-result reconciliation | Implemented as opt-in and used in the Jetson deployment | Deterministic restart/reconnect rehearsals passed; power-loss and all broker-queue cases are not established |
| Jetson deployment | Running at the last verified deployed revision | Uses simulated RES and loopback services reached through an SSH tunnel |
| D02/D03 negative-constraint policy | Implemented and tested locally | Consolidated after the deployed revision; not claimed as deployed or live-model accepted |
| Fine-tuned model candidate | Experimental only | Not the deployed default and not approved as the production model |

## Verified deployment

The last verified Jetson application revision is:

```text
226dd29907fda0916319b7070eb7b055de2c6d69
```

The deployment runs `ucs_app.live:create_integrated_app` with these boundaries:

- UCS on Jetson loopback port 8002;
- a private loopback MQTT broker on port 1884;
- a separate simulated RES process;
- durable UCS and RES SQLite journals in private storage;
- Ollama on Jetson loopback port 11435;
- base model `ministral-3:3b`; and
- access from the user laptop through an SSH tunnel to
  `http://localhost:8002`.

The localhost browser origin supports microphone APIs while SSH encrypts the
remote hop. This is not a public HTTPS deployment. The launcher does not manage
Ollama, change the selected model, enable services at boot, or delete recovery
state. Exact startup, shutdown, rollback and environment instructions are in the
[deployment guide](docs/jetson-deployment.md).

The deployment evidence includes a full Jetson test run, a separate durable
recovery rehearsal, strict typing for the deployed Python version, dependency
checks, startup/readiness checks and simulated live-model attempts. Those results
must not be generalized into physical safety or broad model-accuracy claims.

## Local closeout state versus deployed state

The local `handover/project-closeout` branch consolidates the deployment history,
portable deployment documentation, D02/D03 policy work and later verification
fixes. It is newer than the deployed revision.

Do not claim that the branch's later policy or documentation changes are running
on Jetson. Before any update:

- inspect the actual running process revision and environment;
- inspect pending clarifications and unresolved commands;
- preserve the existing private directory and journals;
- retain `226dd299` as the rollback revision; and
- run a separately approved live policy check after updating.

## Model status and fine-tuning

The deployed default remains `ministral-3:3b`. It is configurable and was selected
provisionally; it has known clarification, intent-fidelity and response-format
limitations. Python validation prevents malformed or inconsistent commands from
being dispatched, but it cannot prove that the model understood the user.

A fine-tuned candidate named `ministral-v2a-sim-20260921:q5_k_m` was trained,
converted, imported and exercised privately on Jetson for the simulated workflow.
The bounded evidence did not justify adopting it as the default: a frozen gate was
not met because of missing-information invention and a superseded-conflict
regression. A separately authorized integrated simulated smoke run succeeded, but
that does not override the failed gate, limited resource headroom or incomplete
comparison criteria. The candidate remains experimental, is not authorized for
physical robot use, and must not silently replace the base model.

Model weights, checkpoints, datasets, raw outputs and detailed private evaluation
records are intentionally outside Git. Issue
[#11](https://github.com/unbee-lee/agentic-pick-place-control/issues/11) should not
be described as fully accepted until the before/after comparison, adoption decision
and agreed evidence requirements are reconciled.

## Speech and privacy

The browser records only after explicit user action. The server validates a bounded
PCM WAV, sends it to Google's recognition endpoint, and returns editable text. A
transcript does not create, confirm or dispatch an arrangement; the user must submit
it through the ordinary request or clarification form.

Known acceptance gaps:

- no recorded user-operated microphone/Google acceptance run on the deployed path;
- no accepted evidence for browser permission and recognition quality;
- the compact interface still lacks the required visible Google-processing
  disclosure; and
- Google's service availability and retention behaviour are external dependencies.

Do not store raw recordings or unrestricted recognition responses by default. See
[the speech guide](docs/speech-input.md).

## Recovery and operational rules

- Never automatically resend a command after an uncertain outcome.
- Never delete or replace the SQLite journals to make a blocked session appear
  healthy.
- Use one UCS worker and one simulator with separate state files.
- Keep the same private state paths across restarts and updates.
- Inspect unresolved execution before stopping, updating or rolling back.
- Treat `/health` as application health, not proof that the model, broker and RES
  are ready.
- Treat `COMPLETED` / `NOT_RUN` as simulated completion only.

The detailed state machine, restart behaviour and remaining recovery evidence are
documented in [recovery and evaluation](docs/recovery-and-evaluation.md).

## Verification at local closeout

The consolidated local branch was verified with:

- 248 automated tests passed;
- strict mypy passed across 40 source files;
- `python -m pip check` reported no broken requirements;
- the Jetson launcher passed shell syntax checking;
- Git whitespace checks passed; and
- targeted scans found no model weights, private notes, runtime databases,
  photographs, credentials, personal paths or Jetson addresses tracked in Git.

The remaining LangGraph pending-deprecation and local LibreSSL warnings are not
test failures, but dependency maintenance remains future work.

## GitHub work state

The repository currently has a stack of draft pull requests rather than one merged
line of all work:

| PR | Purpose | Current relationship |
| --- | --- | --- |
| [#8](https://github.com/unbee-lee/agentic-pick-place-control/pull/8) | Shared Ollama role adapters | Base draft after `main` |
| [#10](https://github.com/unbee-lee/agentic-pick-place-control/pull/10) | Integrated typed workflow | Stacked on #8 |
| [#13](https://github.com/unbee-lee/agentic-pick-place-control/pull/13) | Reviewed speech transcription | Stacked on #10 |
| [#15](https://github.com/unbee-lee/agentic-pick-place-control/pull/15) | Pre-Jetson workflow checkpoint | Historical checkpoint stacked on #13 |
| [#16](https://github.com/unbee-lee/agentic-pick-place-control/pull/16) | Jetson deployment | Stacked on #15 |

PR #9, containing real MQTT and the separate Simulated RES, is already merged.
The local closeout branch is intended to make consolidation and handover review
easier; it has not been pushed or merged at the time of this document update.

Open issues should be resolved by evidence, not by the existence of code alone:

| Issue | Handover interpretation |
| --- | --- |
| [#2](https://github.com/unbee-lee/agentic-pick-place-control/issues/2) | Adapter implementation exists; live acceptance and model behaviour remain qualified |
| [#4](https://github.com/unbee-lee/agentic-pick-place-control/issues/4) | Integrated path exists; complete live acceptance and repair evidence remain incomplete |
| [#5](https://github.com/unbee-lee/agentic-pick-place-control/issues/5) | Speech code exists; disclosure and real microphone/Google acceptance remain open |
| [#6](https://github.com/unbee-lee/agentic-pick-place-control/issues/6) | Durable recovery exists; agreed evaluation and remaining deployment failure cases remain open |
| [#7](https://github.com/unbee-lee/agentic-pick-place-control/issues/7) | Broad intent fidelity, clarification quality and sustained reliability remain unresolved |
| [#11](https://github.com/unbee-lee/agentic-pick-place-control/issues/11) | Training occurred; adoption and complete comparative acceptance remain unresolved |
| [#12](https://github.com/unbee-lee/agentic-pick-place-control/issues/12) | Mixed prose/tool response failure remains a focused open reliability problem |

When cleaning GitHub, update each item with its implemented evidence and explicit
limitations before merging or closing it. If unfinished acceptance is transferred
to future work, link the replacement issue rather than marking unmet criteria as
passed.

## Private and local-only material

The following deliberately remain outside the shared repository:

- model weights and import packages;
- training datasets, checkpoints and raw model outputs;
- detailed evaluation traces and private experiment reports;
- runtime databases, broker persistence, service logs and recordings;
- local demo photographs and presentation videos; and
- personal planning notes and machine-specific access details.

Transfer any approved private artifact separately, with access controls and a
manifest. Do not copy the local preservation folder into Git.

## Recommended next actions

1. Review this closeout branch and complete the documentation/acceptance wording.
2. Create one reviewed consolidation PR or deliberately reconcile the existing PR
   stack; do not merge both paths independently.
3. Update each open issue and draft PR with final evidence and limitations.
4. Merge or close branches only after their commits are recoverable and their
   replacement path is recorded.
5. Reinspect the live Jetson before any update; do not infer its state from local
   Git alone.
6. Synchronize the internship Notion page from the finalized GitHub handover rather
   than treating Notion as an independent source of implementation truth.
