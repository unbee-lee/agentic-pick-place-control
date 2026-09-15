"""Structured decisions accepted from the local language model."""

from enum import Enum
from typing import Annotated, Literal, Mapping, Union

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    TypeAdapter,
    ValidationError,
)

from ucs_contracts.arrangements import TargetArrangement
from ucs_contracts.errors import ContractValidationError

NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class ProposeDecision(BaseModel):
    """A complete candidate arrangement subject to application delivery rules."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Literal["PROPOSE"]
    target_positions: TargetArrangement
    user_message: NonEmptyText


class ClarifyDecision(BaseModel):
    """A focused question needed before an arrangement can be proposed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Literal["CLARIFY"]
    question: NonEmptyText


class RejectReasonCode(str, Enum):
    """Stable application reason for rejecting an arrangement request."""

    OUT_OF_SCOPE = "OUT_OF_SCOPE"
    INVALID_REQUEST = "INVALID_REQUEST"


class RejectDecision(BaseModel):
    """A request the UCS cannot safely turn into an arrangement."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    decision: Literal["REJECT"]
    reason_code: RejectReasonCode
    user_message: NonEmptyText


Decision = Annotated[
    Union[ProposeDecision, ClarifyDecision, RejectDecision],
    Field(discriminator="decision"),
]
_DECISION_ADAPTER: TypeAdapter[Decision] = TypeAdapter(Decision)


def parse_decision(payload: Mapping[str, object]) -> Decision:
    """Parse an application-validated structured model response."""

    try:
        return _DECISION_ADAPTER.validate_python(dict(payload))
    except ValidationError as error:
        raise ContractValidationError("invalid model decision") from error
