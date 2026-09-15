"""Strict role actions; semantic target validation remains a separate gate."""

from typing import Annotated, Dict, Literal, Union

from ucs_contracts.arrangements import FrontPosition

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, TypeAdapter

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000)]


class Action(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InterpretedAssignment(Action):
    """Explicit intent; duplicate destinations remain available for clarification."""

    E: FrontPosition
    B: FrontPosition
    H: FrontPosition


class Arrange(Action):
    action: Literal["ARRANGE"]
    assignment: InterpretedAssignment


class Clarify(Action):
    action: Literal["CLARIFY"]
    question: Text


class Validate(Action):
    action: Literal["VALIDATE"]
    command: Dict[str, object]


class AskUser(Action):
    action: Literal["ASK_USER"]
    question: Text


CommandAction = Annotated[Union[Arrange, Clarify], Field(discriminator="action")]
RobotAction = Annotated[Union[Validate, AskUser], Field(discriminator="action")]
command_action: TypeAdapter[CommandAction] = TypeAdapter(CommandAction)
robot_action: TypeAdapter[RobotAction] = TypeAdapter(RobotAction)
