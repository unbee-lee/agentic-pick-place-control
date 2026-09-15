"""SUBACK failure codes must stop work even when subscribe() returns normally."""

import asyncio
from unittest.mock import MagicMock, patch
from uuid import uuid4

import aiomqtt
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.reasoncodes import ReasonCode
import pytest

from ucs_app.mqtt import MqttConfig, MqttError, MqttRobotExecutionStation
from ucs_app.simulated_res import SimulatedRES, SimulationConfig
from ucs_app.transport import utc_timestamp


def codes(*values: int) -> list[ReasonCode]:
    return [ReasonCode(PacketTypes.SUBACK, identifier=value) for value in values]


@pytest.mark.parametrize("acknowledgements", [
    (128, 128), (1, 128), (128, 1), (), (1,), (1, 1, 1),
    codes(128, 135), codes(1, 135), codes(135, 1),
])
def test_rejected_or_incomplete_subscriptions_prevent_publication(acknowledgements: object) -> None:
    async def run() -> None:
        client = MagicMock(spec=aiomqtt.Client)
        client.__aenter__.return_value = client
        client.subscribe.return_value = acknowledgements
        with patch.object(MqttConfig, "client", return_value=client):
            res = MqttRobotExecutionStation(MqttConfig())
            command: dict[str, object] = {
                "schema_version": "1.0", "message_id": str(uuid4()), "type": "ARRANGE",
                "created_at": utc_timestamp(),
                "target_positions": {"E": "front_left", "B": "front_center", "H": "front_right"},
            }
            with pytest.raises(MqttError, match="command not published"):
                async for _ in res.execute(command):
                    pytest.fail("Rejected subscriptions yielded an update")
        client.publish.assert_not_called()
        client.__aexit__.assert_awaited_once()
    asyncio.run(run())


@pytest.mark.parametrize("acknowledgements", [(0, 1), (1, 1), codes(0, 1), codes(1, 1)])
def test_granted_subscriptions_allow_publication(acknowledgements: object) -> None:
    async def run() -> None:
        client = MagicMock(spec=aiomqtt.Client)
        client.__aenter__.return_value = client
        client.subscribe.return_value = acknowledgements
        client.messages.__aiter__.return_value = []
        with patch.object(MqttConfig, "client", return_value=client):
            res = MqttRobotExecutionStation(MqttConfig())
            command: dict[str, object] = {
                "schema_version": "1.0", "message_id": str(uuid4()), "type": "ARRANGE",
                "created_at": utc_timestamp(),
                "target_positions": {"E": "front_left", "B": "front_center", "H": "front_right"},
            }
            async for _ in res.execute(command):
                pass
        client.publish.assert_awaited_once()
    asyncio.run(run())


@pytest.mark.parametrize("acknowledgements", [(128,), (), (1, 1), codes(135)])
def test_simulator_does_not_report_ready_after_rejection(acknowledgements: object, capsys: pytest.CaptureFixture[str]) -> None:
    async def run() -> None:
        client = MagicMock(spec=aiomqtt.Client)
        client.__aenter__.return_value = client
        client.subscribe.return_value = acknowledgements
        with patch.object(MqttConfig, "client", return_value=client):
            with pytest.raises(MqttError, match="command subscription was not granted"):
                await SimulatedRES(SimulationConfig()).serve(MqttConfig())
        client.publish.assert_not_called()
        client.__aexit__.assert_awaited_once()
    asyncio.run(run())
    assert "ready" not in capsys.readouterr().out
