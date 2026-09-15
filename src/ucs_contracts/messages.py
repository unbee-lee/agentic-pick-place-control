"""Validation entry point for public Robot Execution Station messages."""

import json
from functools import lru_cache
from importlib.resources import files
from typing import Dict, Literal, Mapping, cast

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError

from ucs_contracts.errors import ContractValidationError

MessageType = Literal["command", "status", "result"]

_SCHEMA_FILES = {
    "command": "robot-command.schema.json",
    "status": "robot-status.schema.json",
    "result": "robot-result.schema.json",
}


@lru_cache(maxsize=3)
def _load_schema(message_type: MessageType) -> Dict[str, object]:
    filename = _SCHEMA_FILES.get(message_type)
    if filename is None:
        raise ContractValidationError(f"unknown message type: {message_type}")

    schema_text = (
        files("ucs_contracts.schemas")
        .joinpath(filename)
        .read_text(encoding="utf-8")
    )
    schema: object = json.loads(schema_text)
    if not isinstance(schema, dict):
        raise RuntimeError(f"schema {filename} must contain a JSON object")
    return cast(Dict[str, object], schema)


def validate_message(
    message_type: MessageType,
    payload: Mapping[str, object],
) -> None:
    """Validate a payload against one named public message schema."""

    schema = _load_schema(message_type)
    try:
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(
            schema,
            format_checker=FormatChecker(),
        )
        validator.validate(dict(payload))
    except (SchemaError, ValidationError) as error:
        raise ContractValidationError(
            f"invalid {message_type} message"
        ) from error
