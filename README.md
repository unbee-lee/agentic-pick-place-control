# Human-in-the-Loop Agentic Control for Pick-and-Place Robotics

A browser-based User Command Station (UCS) for expressing arrangements of an elephant, bear, and hippo across three front positions. The project focuses on command interpretation and interaction with a Simulated Robot Execution Station (RES). Physical robot control, ROS, and visual verification are outside its scope.

The [system design](docs/design.md) defines the two-agent workflow and automatic delivery of validated targets. The [controlled composition](docs/controlled-ucs.md) is the runnable development harness; its deterministic role adapters, explicit assignment handoff, bounded command repair, clarification path, and automatic delivery are documented there.

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

- [Design](docs/design.md): roles, architecture, validation, and execution boundary.
- [Contracts](docs/contracts.md): Python validation entry points and message schemas.
- [Controlled composition](docs/controlled-ucs.md): exact harness behaviour and browser acceptance test.
- [Live Ollama composition](docs/live-ollama.md): configurable shared-model roles, simulated RES, and live smoke check.

The opt-in live composition connects both roles to one Ollama model. MQTT with a separate Simulated RES, browser speech input, and Jetson/HTTPS deployment remain follow-up work. The controlled harness verifies application routing and safeguards, not model accuracy or physical safety. Broader live-model reliability remains under evaluation in issue #7.

GitHub issues track implementation work; pull requests record changes and verification.

## Project origins

This project continues User Command Station development undertaken in a fork of the [ICP intelligent pick-and-place robot project](https://github.com/InnovationCentralPerth/intelligent-pick-place-robot). It begins with a clean snapshot of selected UCS code, contracts, tests, and technical documentation. The inherited physical-controller implementation is excluded. The initial import commit records the source snapshot.
