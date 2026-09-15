"""Standalone simulated RES. Run with python -m ucs_app.simulated_res."""

import asyncio
import json
import os
from typing import Mapping, cast
from uuid import UUID

import aiomqtt
from pydantic import BaseModel, ConfigDict, Field

from ucs_app.controlled import ControlledRobotExecutionStation
from ucs_app.mqtt import COMMAND_TOPIC, RESULT_TOPIC, STATUS_TOPIC, MqttConfig, MqttError, decode_payload
from ucs_app.transport import utc_timestamp
from ucs_contracts import ContractValidationError, validate_message, validate_target_positions


class SimulationConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    delay_seconds: float = Field(default=0.75, ge=0, le=300)
    max_commands: int = Field(default=4096, ge=1, le=100000)


def rejection(message_id: str) -> dict[str, object]:
    result: dict[str, object] = {
        "schema_version": "1.0", "message_id": message_id, "status": "DONE",
        "execution": {"status": "NOT_STARTED", "error": {
            "code": "INVALID_COMMAND", "message": "Command schema or arrangement is invalid"}},
        "verification": {"status": "NOT_RUN", "outcome": None,
                         "observed_positions": None, "error": None},
        "completed_at": utc_timestamp(),
    }
    validate_message("result", result)
    return result


class SimulatedRES:
    """One sequential executor; never evict identifiers and accidentally replay them."""

    def __init__(self, config: SimulationConfig) -> None:
        self._config = config
        self._seen: set[str] = set()
        self._simulation = ControlledRobotExecutionStation(result_delay_seconds=config.delay_seconds)

    async def handle(self, client: aiomqtt.Client, message: aiomqtt.Message) -> None:
        if message.retain:
            return
        try:
            command = decode_payload(message.payload)
            message_id = command.get("message_id")
            if not isinstance(message_id, str):
                return
            identity = str(UUID(message_id))
            if message_id.lower() != identity:
                return
        except (MqttError, ValueError):
            return  # No usable correlation identifier; do not fabricate a result.
        if identity in self._seen:
            return  # Includes altered payloads with a previously seen identifier.
        if len(self._seen) >= self._config.max_commands:
            raise MqttError("Simulator command limit reached; operator review required")
        self._seen.add(identity)  # Reserve before validation, work, or publication.
        try:
            validate_message("command", command)
            validate_target_positions(cast(Mapping[str, object], command["target_positions"]))
        except ContractValidationError:
            await client.publish(RESULT_TOPIC, json.dumps(rejection(message_id)), qos=1, retain=False)
            return
        async for update in self._simulation.execute(command):
            validate_message(update.message_type, update.payload)
            topic = STATUS_TOPIC if update.message_type == "status" else RESULT_TOPIC
            await client.publish(topic, json.dumps(dict(update.payload)), qos=1, retain=False)

    async def serve(self, config: MqttConfig) -> None:
        async with config.client() as client:
            acknowledgements = await client.subscribe(COMMAND_TOPIC, qos=1)
            if len(acknowledgements) != 1 or acknowledgements[0] not in (0, 1, 2):
                raise MqttError("MQTT command subscription was not granted")
            print("Simulated RES ready", flush=True)
            async for message in client.messages:
                await self.handle(client, message)


def main() -> None:
    try:
        simulation = SimulationConfig(delay_seconds=float(os.getenv("UCS_RES_DELAY", "0.75")))
        asyncio.run(SimulatedRES(simulation).serve(MqttConfig.from_env()))
    except (aiomqtt.MqttError, MqttError):
        print("Simulated RES stopped: MQTT failure or command limit; inspect service state before restarting", flush=True)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
