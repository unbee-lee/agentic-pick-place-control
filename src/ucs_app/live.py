"""Opt-in local-model composition; RES remains explicitly simulated."""

from fastapi import FastAPI

from ucs_app.app import create_app
from ucs_app.controlled import ControlledRobotExecutionStation
from ucs_app.ollama import OllamaClient, OllamaConfig, OllamaRobotCommandAgent, OllamaUserCommandAgent


def create_live_app() -> FastAPI:
    config = OllamaConfig.from_env()
    client = OllamaClient(config)
    return create_app(
        user_command_agent=OllamaUserCommandAgent(client),
        robot_command_agent=OllamaRobotCommandAgent(client),
        robot_execution_station=ControlledRobotExecutionStation(result_delay_seconds=0.75),
        composition_label="Local model development — simulated RES",
        agent_timeout=config.timeout_seconds + 1,
    )
