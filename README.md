# Human-in-the-Loop Agentic Control for Pick-and-Place Robotics

A browser-based User Command Station (UCS) for expressing arrangements of an elephant, bear, and hippo across three front positions. Two sequential local-LLM roles interpret the request and prepare a command; Python validates the exact target before MQTT delivery to a separate Simulated Robot Execution Station (RES). Physical robot control, ROS, and visual verification are outside this repository's scope.

Start with [the project handover](HANDOVER.md) for the implementation, deployment, evidence boundaries, known limitations, GitHub work state, and recommended next actions. The [system design](docs/design.md) defines the workflow, while the [controlled composition](docs/controlled-ucs.md) provides a deterministic development harness.

## Current status

- The controlled, live-model, MQTT, speech and opt-in durable-recovery paths are implemented.
- The last verified Jetson application revision is `226dd299`; it uses the base `ministral-3:3b` model and a separate Simulated RES.
- Later D02/D03 policy and publication-review changes are merged into `main` but are not claimed as deployed.
- Real microphone/Google acceptance, broad live-model reliability, agreed evaluation completion and some recovery failure cases remain open.
- A fine-tuned candidate exists as private experimental evidence; it is not the deployed default or an accepted production model.

## Run

Use Python 3.9 or later from the repository root:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/uvicorn 'ucs_app.controlled:create_controlled_app' --factory --host 127.0.0.1 --port 8000
```

Open <http://127.0.0.1:8000>. The controlled harness requires no model service, broker, or robot.

## Checks

```bash
.venv/bin/python -m playwright install chromium
.venv/bin/pytest
.venv/bin/mypy
.venv/bin/python -m pip check
```

## Documentation

- [Project handover](HANDOVER.md): current implementation, deployment, limitations, GitHub state and next actions.
- [Isolated Jetson deployment](docs/jetson-deployment.md): startup, SSH access, recovery checks and rollback.
- [Historical pre-Jetson workflow checkpoint](docs/archive/pre-jetson-workflow-checkpoint.md): the preserved state before deployment, retained for traceability rather than current status.
- [Speech input](docs/speech-input.md): reviewed microphone transcription and deployment requirements.
- [Design](docs/design.md): roles, architecture, validation, and execution boundary.
- [Contracts](docs/contracts.md): Python validation entry points and message schemas.
- [Controlled composition](docs/controlled-ucs.md): exact harness behaviour and browser acceptance test.
- [Live Ollama composition](docs/live-ollama.md): configurable shared-model roles, simulated RES, and live smoke check.
- [MQTT composition](docs/mqtt-res.md): real broker, separate simulated RES, configuration, and integration checks.
- [Recovery and evaluation](docs/recovery-and-evaluation.md): opt-in durable journals, restart/late-result semantics, and proposed assessment criteria.
- [Integrated workflow](docs/integrated-workflow.md): both live roles with MQTT, separate RES, and separately reported deterministic/live checks.

The documentation distinguishes implemented code, deterministic verification,
deployment evidence and acceptance. Passing validation or simulated execution does
not establish model accuracy, physical safety or visual verification. GitHub issues
track remaining acceptance work; pull requests record changes and verification.

## Project origins

This project continues User Command Station development undertaken in a fork of the [ICP intelligent pick-and-place robot project](https://github.com/InnovationCentralPerth/intelligent-pick-place-robot). It begins with a clean snapshot of selected UCS code, contracts, tests, and technical documentation. The inherited physical-controller implementation is excluded. The initial import commit records the source snapshot.
