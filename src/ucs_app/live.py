"""Opt-in local-model composition; RES remains explicitly simulated."""

from fastapi import FastAPI

from ucs_app.app import create_app
from ucs_app.controlled import ControlledRobotExecutionStation
from ucs_app.ollama import OllamaClient, OllamaConfig, OllamaRobotCommandAgent, OllamaUserCommandAgent
from ucs_app.mqtt import MqttConfig, MqttRobotExecutionStation


def create_live_app() -> FastAPI:
    config = OllamaConfig.from_env()
    client = OllamaClient(config)
    return create_app(
        user_command_agent=OllamaUserCommandAgent(client),
        robot_command_agent=OllamaRobotCommandAgent(client),
        robot_execution_station=ControlledRobotExecutionStation(result_delay_seconds=5.0),
        composition_label="Local model development — simulated RES",
        agent_timeout=config.timeout_seconds + 1,
    )


def create_integrated_app() -> FastAPI:
    """Live model roles over MQTT to a separately started simulated RES."""
    model = OllamaConfig.from_env()
    broker = MqttConfig.from_env()
    res = MqttRobotExecutionStation(broker)
    client = OllamaClient(model)
    return create_app(
        user_command_agent=OllamaUserCommandAgent(client),
        robot_command_agent=OllamaRobotCommandAgent(client),
        robot_execution_station=res,
        result_listener=res.listen_results,
        composition_label="Integrated development — local model, MQTT, separate simulated RES",
        agent_timeout=model.timeout_seconds + 1,
        execution_timeout=broker.execution_timeout,
    )
