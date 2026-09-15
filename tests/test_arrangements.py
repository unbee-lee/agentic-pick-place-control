from itertools import permutations

import pytest

from ucs_contracts import (
    ContractValidationError,
    validate_message,
    validate_target_positions,
)


@pytest.mark.parametrize(
    "positions",
    permutations(("front_left", "front_center", "front_right")),
)
def test_all_six_target_arrangements_pass_both_validation_layers(
    positions: tuple[str, str, str],
) -> None:
    payload = dict(zip(("E", "B", "H"), positions))
    command = {
        "schema_version": "1.0",
        "message_id": "76f47e9d-3df4-4d66-8899-73f65b852575",
        "type": "ARRANGE",
        "target_positions": payload,
        "created_at": "2026-09-01T09:00:00Z",
    }

    arrangement = validate_target_positions(payload)
    validate_message("command", command)

    assert arrangement.model_dump(mode="json") == payload


def test_duplicate_target_position_is_rejected() -> None:
    payload = {
        "E": "front_left",
        "B": "front_left",
        "H": "front_right",
    }

    with pytest.raises(ContractValidationError):
        validate_target_positions(payload)


@pytest.mark.parametrize(
    "payload",
    [
        {"E": "front_left", "B": "front_center"},
        {
            "E": "front_left",
            "B": "front_center",
            "H": "front_right",
            "L": "front_left",
        },
        {
            "E": "back_left",
            "B": "front_center",
            "H": "front_right",
        },
    ],
    ids=["missing-animal", "unknown-animal", "internal-position"],
)
def test_invalid_target_arrangements_are_rejected(
    payload: dict[str, str],
) -> None:
    with pytest.raises(ContractValidationError):
        validate_target_positions(payload)
