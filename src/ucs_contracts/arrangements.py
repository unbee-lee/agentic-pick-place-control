"""Target-arrangement contract for the User Command Station."""

from enum import Enum
from typing import Mapping

from pydantic import BaseModel, ConfigDict, ValidationError, model_validator

from ucs_contracts.errors import ContractValidationError


class FrontPosition(str, Enum):
    """A destination visible at the UCS interface."""

    FRONT_LEFT = "front_left"
    FRONT_CENTER = "front_center"
    FRONT_RIGHT = "front_right"


class TargetArrangement(BaseModel):
    """A complete assignment of animals to UCS-visible positions."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    E: FrontPosition
    B: FrontPosition
    H: FrontPosition

    @model_validator(mode="after")
    def positions_are_unique(self) -> "TargetArrangement":
        """Require a one-to-one animal-to-position assignment."""

        positions = {self.E, self.B, self.H}
        if len(positions) != 3:
            raise ValueError("each animal must occupy a different front position")
        return self


def validate_target_positions(payload: Mapping[str, object]) -> TargetArrangement:
    """Parse a target-position mapping into the domain contract."""

    try:
        return TargetArrangement.model_validate(dict(payload))
    except ValidationError as error:
        raise ContractValidationError("invalid target arrangement") from error
