# UCS interface contracts

The `ucs_contracts` package is the executable interface between free-form model
output, deterministic UCS application logic, and the Robot Execution Station (RES).
It does not implement the UCS runtime, MQTT transport, or the Simulated Robot Execution
Station.

## Public Python interface

Import the three validation entry points from the package root:

```python
from ucs_contracts import (
    parse_decision,
    validate_message,
    validate_target_positions,
)
```

- `parse_decision(payload)` returns one typed `PROPOSE`, `CLARIFY`, or `REJECT`
  decision.
- `validate_target_positions(payload)` returns a typed target arrangement and
  enforces a one-to-one assignment of `E`, `B`, and `H` to the three front
  positions.
- `validate_message(message_type, payload)` validates a `command`, `status`, or
  `result` payload against its public JSON Schema.

Invalid input raises `ucs_contracts.ContractValidationError`. Callers should
handle that stable package exception rather than depending on Pydantic or
jsonschema exceptions.

The three decision payloads remain available for contract compatibility. The
runtime uses the strict role actions in `ucs_app.actions`: the User Command
Agent selects `ARRANGE` with an explicit `assignment` or `CLARIFY`; the Robot Command Agent selects `VALIDATE` with a candidate `command`
or `ASK_USER`. Unknown fields and actions are rejected. `VALIDATE` carries an
unvalidated command so the Python gate can return schema, arrangement, metadata, or assignment-consistency errors to the
Robot Command Agent. `ARRANGE.assignment` contains E, B and H at allowed front positions; duplicate destinations remain representable so conflicting intent can be clarified rather than silently changed. See [the design](design.md).

## Validation layers

JSON Schema checks message structure: required properties, types, constants,
enums, UUIDs, RFC 3339 timestamps, and unknown properties. Application
validation checks domain meaning that is awkward or stateful, including the
one-animal-per-position invariant, request eligibility, active-message
correlation, duplicate-message handling, and stale-response protection.

A command should therefore pass both layers before publication:

```python
arrangement = validate_target_positions(command_payload["target_positions"])
validate_message("command", command_payload)
```

The second call does not replace the first. For example, each position value
can be schema-valid while two animals still select the same position.

## Packaged message schemas

- `src/ucs_contracts/schemas/robot-command.schema.json` validates messages for
  `robot/command`.
- `src/ucs_contracts/schemas/robot-status.schema.json` validates non-terminal
  `BUSY` messages for `robot/status`.
- `src/ucs_contracts/schemas/robot-result.schema.json` validates terminal
  `DONE` messages for `robot/result`.

`DONE` means processing terminated; execution and verification still report
their results independently. A normal simulated success uses execution
`COMPLETED` and verification `NOT_RUN`. `BACKEND_TIMEOUT` and
`BACKEND_UNAVAILABLE` remain UCS-local states and are not valid RES error codes.

## Development checks

Create an isolated environment and install the package with its development
dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
```

Run focused tests while changing a seam, static typing regularly, and the full
suite before review:

```bash
.venv/bin/pytest tests/test_arrangements.py -q
.venv/bin/pytest tests/test_decisions.py -q
.venv/bin/pytest tests/test_messages.py -q
.venv/bin/mypy
.venv/bin/pytest
```
