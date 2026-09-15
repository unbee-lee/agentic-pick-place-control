import pytest

from ucs_contracts import (
    ContractValidationError,
    parse_decision,
)


def test_parse_propose_decision() -> None:
    payload = {
        "decision": "PROPOSE",
        "target_positions": {
            "E": "front_left",
            "B": "front_center",
            "H": "front_right",
        },
        "user_message": "I can arrange the animals this way.",
    }

    decision = parse_decision(payload)

    assert decision.decision == "PROPOSE"
    assert decision.target_positions.model_dump(mode="json") == payload[
        "target_positions"
    ]


def test_parse_clarify_decision() -> None:
    payload = {
        "decision": "CLARIFY",
        "question": "Which animal should be in the centre?",
    }

    decision = parse_decision(payload)

    assert decision.decision == "CLARIFY"
    assert decision.question == payload["question"]


def test_parse_reject_decision() -> None:
    payload = {
        "decision": "REJECT",
        "reason_code": "OUT_OF_SCOPE",
        "user_message": "I can only arrange the three supported animals.",
    }

    decision = parse_decision(payload)

    assert decision.decision == "REJECT"
    assert decision.reason_code.value == "OUT_OF_SCOPE"


def test_reject_decision_can_identify_an_invalid_request() -> None:
    payload = {
        "decision": "REJECT",
        "reason_code": "INVALID_REQUEST",
        "user_message": "That request does not describe a valid arrangement.",
    }

    decision = parse_decision(payload)

    assert decision.decision == "REJECT"
    assert decision.reason_code.value == "INVALID_REQUEST"


@pytest.mark.parametrize(
    "forbidden_field",
    [
        "message_id",
        "mqtt_topic",
        "created_at",
        "scenario",
        "reasoning",
        "confidence",
    ],
)
def test_propose_rejects_transport_and_internal_metadata(
    forbidden_field: str,
) -> None:
    payload = {
        "decision": "PROPOSE",
        "target_positions": {
            "E": "front_left",
            "B": "front_center",
            "H": "front_right",
        },
        "user_message": "I can arrange the animals this way.",
        forbidden_field: "not allowed",
    }

    with pytest.raises(ContractValidationError):
        parse_decision(payload)


def test_propose_rejects_duplicate_target_positions() -> None:
    payload = {
        "decision": "PROPOSE",
        "target_positions": {
            "E": "front_left",
            "B": "front_left",
            "H": "front_right",
        },
        "user_message": "I can arrange the animals this way.",
    }

    with pytest.raises(ContractValidationError):
        parse_decision(payload)


def test_unknown_decision_is_rejected() -> None:
    with pytest.raises(ContractValidationError):
        parse_decision({"decision": "PUBLISH"})
