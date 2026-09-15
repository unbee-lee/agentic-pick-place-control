from ucs_contracts import (
    parse_decision,
    validate_message,
    validate_target_positions,
)


def test_contract_entry_points_are_public() -> None:
    assert callable(parse_decision)
    assert callable(validate_message)
    assert callable(validate_target_positions)
