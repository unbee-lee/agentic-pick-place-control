"""MQTT execution boundary: subscribe first, publish once, correlate replies."""

import json
import os
import ssl
from typing import AsyncIterator, Literal, Mapping, Optional, cast

import aiomqtt
from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from ucs_app.app import create_app
from ucs_app.controlled import ControlledRobotCommandAgent, ControlledUserCommandAgent
from ucs_app.interfaces import AdapterFailure, ControllerUpdate
from ucs_contracts import validate_message, validate_target_positions

COMMAND_TOPIC = "robot/command"
STATUS_TOPIC = "robot/status"
RESULT_TOPIC = "robot/result"
MAX_PAYLOAD_BYTES = 65536


class MqttError(AdapterFailure):
    """Bounded transport failure without broker credentials or payloads."""

    public_hint = "Check broker connectivity and permissions and the simulated RES; establish the execution outcome before retrying."


class MqttConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)
    host: str = Field(default="127.0.0.1", min_length=1)
    port: int = Field(default=1883, ge=1, le=65535)
    username: Optional[str] = Field(default=None, repr=False)
    password: Optional[SecretStr] = None
    tls_ca: Optional[str] = None
    operation_timeout: float = Field(default=5, gt=0, le=60)
    execution_timeout: float = Field(default=30, gt=0, le=300)

    @classmethod
    def from_env(cls) -> "MqttConfig":
        password = os.getenv("UCS_MQTT_PASSWORD")
        return cls(host=os.getenv("UCS_MQTT_HOST", "127.0.0.1"),
                   port=int(os.getenv("UCS_MQTT_PORT", "1883")),
                   username=os.getenv("UCS_MQTT_USERNAME"),
                   password=SecretStr(password) if password is not None else None,
                   tls_ca=os.getenv("UCS_MQTT_TLS_CA"),
                   operation_timeout=float(os.getenv("UCS_MQTT_OPERATION_TIMEOUT", "5")),
                   execution_timeout=float(os.getenv("UCS_MQTT_EXECUTION_TIMEOUT", "30")))

    def client(self) -> aiomqtt.Client:
        return aiomqtt.Client(
            hostname=self.host, port=self.port, username=self.username,
            password=self.password.get_secret_value() if self.password else None,
            tls_context=ssl.create_default_context(cafile=self.tls_ca) if self.tls_ca else None,
            timeout=self.operation_timeout, max_queued_incoming_messages=256,
        )


def decode_payload(payload: object) -> dict[str, object]:
    if not isinstance(payload, (str, bytes, bytearray)) or len(payload) > MAX_PAYLOAD_BYTES:
        raise MqttError("Invalid MQTT payload")
    try:
        value = json.loads(payload)
    except (ValueError, UnicodeError, RecursionError):
        raise MqttError("Invalid MQTT JSON") from None
    if not isinstance(value, dict):
        raise MqttError("MQTT payload must be an object")
    return cast(dict[str, object], value)


class MqttRobotExecutionStation:
    def __init__(self, config: MqttConfig) -> None:
        self._config = config

    async def execute(self, command: Mapping[str, object]) -> AsyncIterator[ControllerUpdate]:
        validate_message("command", command)
        validate_target_positions(cast(Mapping[str, object], command["target_positions"]))
        try:
            async with self._config.client() as client:
                acknowledgements = await client.subscribe([(STATUS_TOPIC, 1), (RESULT_TOPIC, 1)])
                # subscribe() returns SUBACK failures without raising. Require a
                # granted QoS for each topic before a command can leave UCS.
                if len(acknowledgements) != 2 or any(code not in (0, 1, 2) for code in acknowledgements):
                    raise MqttError("MQTT status/result subscriptions were not granted; command not published")
                await client.publish(COMMAND_TOPIC, json.dumps(dict(command)), qos=1, retain=False)
                async for message in client.messages:
                    if message.retain:
                        continue
                    payload = decode_payload(message.payload)
                    if payload.get("message_id") != command["message_id"]:
                        continue
                    kind: Literal["status", "result"] = "status" if str(message.topic) == STATUS_TOPIC else "result"
                    validate_message(kind, payload)
                    yield ControllerUpdate(kind, payload)
                    if kind == "result":
                        return
        except aiomqtt.MqttError:
            raise MqttError("MQTT connection or acknowledgement failed") from None


def create_mqtt_app() -> FastAPI:
    """Controlled role agents, real broker, separate simulated RES."""
    config = MqttConfig.from_env()
    return create_app(
        user_command_agent=ControlledUserCommandAgent(),
        robot_command_agent=ControlledRobotCommandAgent(),
        robot_execution_station=MqttRobotExecutionStation(config),
        composition_label="MQTT development — controlled agents, separate simulated RES",
        execution_timeout=config.execution_timeout,
    )
