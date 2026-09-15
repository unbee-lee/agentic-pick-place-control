import json
from pathlib import Path
from typing import Dict, Literal, cast

import pytest

from ucs_contracts import ContractValidationError, validate_message

MessageType = Literal["command", "status", "result"]


FIXTURES = Path(__file__).parent / "fixtures" / "messages"


def load_fixture(filename: str) -> Dict[str, object]:
    payload: object = json.loads((FIXTURES / filename).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return cast(Dict[str, object], payload)


def test_valid_command_message_matches_schema() -> None:
    validate_message("command", load_fixture("valid-command.json"))


def test_valid_busy_status_matches_schema() -> None:
    validate_message("status", load_fixture("valid-status.json"))


def test_successful_simulated_result_matches_schema() -> None:
    validate_message("result", load_fixture("valid-result-success.json"))


def test_completed_execution_with_error_is_rejected() -> None:
    payload = load_fixture("invalid-result-completed-with-error.json")

    with pytest.raises(ContractValidationError):
        validate_message("result", payload)


def test_rejected_command_result_matches_schema() -> None:
    validate_message("result", load_fixture("valid-result-rejected.json"))


def test_not_started_execution_without_error_is_rejected() -> None:
    payload = load_fixture("invalid-result-not-started-without-error.json")

    with pytest.raises(ContractValidationError):
        validate_message("result", payload)


def test_not_run_verification_cannot_claim_an_outcome() -> None:
    payload = load_fixture("invalid-result-not-run-with-outcome.json")

    with pytest.raises(ContractValidationError):
        validate_message("result", payload)


def test_completed_verification_without_outcome_is_rejected() -> None:
    payload = load_fixture("invalid-result-completed-verification-no-outcome.json")

    with pytest.raises(ContractValidationError):
        validate_message("result", payload)


def test_failed_verification_without_error_is_rejected() -> None:
    payload = load_fixture("invalid-result-failed-verification-no-error.json")

    with pytest.raises(ContractValidationError):
        validate_message("result", payload)


def test_not_started_execution_rejects_execution_failed_code() -> None:
    payload = load_fixture("invalid-result-not-started-execution-failed-code.json")

    with pytest.raises(ContractValidationError):
        validate_message("result", payload)


def test_failed_execution_rejects_invalid_command_code() -> None:
    payload = load_fixture("invalid-result-failed-invalid-command-code.json")

    with pytest.raises(ContractValidationError):
        validate_message("result", payload)


@pytest.mark.parametrize(
    ("message_type", "filename"),
    [
        ("command", "invalid-command-malformed-uuid.json"),
        ("command", "invalid-command-malformed-timestamp.json"),
        ("command", "invalid-command-rear-position.json"),
        ("command", "invalid-command-missing-animal.json"),
        ("command", "invalid-command-unknown-field.json"),
        ("status", "invalid-status-enum.json"),
        ("status", "invalid-status-unknown-field.json"),
        ("result", "invalid-result-ucs-local-error-code.json"),
    ],
)
def test_invalid_message_fixtures_are_rejected(
    message_type: MessageType,
    filename: str,
) -> None:
    with pytest.raises(ContractValidationError):
        validate_message(message_type, load_fixture(filename))
