"""FastAPI interface for the browser-observable User Command Station."""

import asyncio
import json
from importlib.resources import files
from typing import AsyncIterator
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from ucs_app.interfaces import RobotCommandAgent, RobotExecutionStation, UserCommandAgent
from ucs_app.sessions import BrowserSession, SessionStore
from ucs_app.workflow import UcsWorkflow, WorkflowConflict, WorkflowFailure

_SESSION_COOKIE = "ucs_session"


class ArrangementRequestBody(BaseModel):
    """Typed request submitted through the browser interface."""

    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=4000)

    @field_validator("text")
    @classmethod
    def text_is_not_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Arrangement request cannot be empty")
        return stripped


class NewRequestBody(ArrangementRequestBody):
    request_id: UUID


class ReplyBody(ArrangementRequestBody):
    workflow_id: UUID
    turn: int = Field(ge=1)


class CancelBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    workflow_id: UUID
    turn: int = Field(ge=1)


def create_app(
    *,
    user_command_agent: UserCommandAgent,
    robot_command_agent: RobotCommandAgent,
    robot_execution_station: RobotExecutionStation,
    composition_label: str,
    agent_timeout: float = 20,
    max_repairs: int = 2,
) -> FastAPI:
    """Build a UCS from explicit adapters at its two external seams."""

    sessions = SessionStore()
    workflow = UcsWorkflow(
        user_command_agent=user_command_agent,
        robot_command_agent=robot_command_agent,
        robot_execution_station=robot_execution_station,
        sessions=sessions,
        agent_timeout=agent_timeout,
        max_repairs=max_repairs,
    )
    application = FastAPI(title="User Command Station")

    @application.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        session = sessions.get(request.cookies.get(_SESSION_COOKIE))
        if session is None:
            session = sessions.create()

        html = (
            files("ucs_app")
            .joinpath("web/index.html")
            .read_text(encoding="utf-8")
            .replace("{{COMPOSITION_LABEL}}", composition_label)
        )
        response = HTMLResponse(html)
        response.set_cookie(
            _SESSION_COOKIE,
            session.session_id,
            httponly=True,
            samesite="lax",
        )
        return response

    @application.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "composition": composition_label}

    @application.get("/events")
    async def events(request: Request) -> StreamingResponse:
        session = _require_session(request, sessions)
        # Register and capture synchronously: updates after this snapshot enter
        # the queue, so reconnect cannot miss a change between read and subscribe.
        subscriber = session.subscribe()
        snapshot = session.snapshot()

        async def event_stream() -> AsyncIterator[str]:
            try:
                yield f"event: snapshot\ndata: {json.dumps(snapshot)}\n\n"
                while True:
                    try:
                        event = await asyncio.wait_for(subscriber.get(), timeout=15)
                    except asyncio.TimeoutError:
                        yield ": keepalive\n\n"
                        continue
                    yield event.as_sse()
            finally:
                session.unsubscribe(subscriber)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @application.exception_handler(WorkflowConflict)
    async def conflict_handler(request: Request, error: WorkflowConflict) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(error)})

    @application.exception_handler(WorkflowFailure)
    async def failure_handler(request: Request, error: WorkflowFailure) -> JSONResponse:
        return JSONResponse(status_code=503, content={"detail": str(error)})

    def state_response(session: BrowserSession) -> dict[str, object]:
        return {"status": "awaiting_clarification" if session.awaiting_reply else "completed",
                "workflow_id": session.workflow_id, "turn": session.clarification_turn}

    @application.post("/arrangement-requests", status_code=status.HTTP_202_ACCEPTED)
    async def submit_arrangement_request(body: NewRequestBody, request: Request) -> dict[str, object]:
        session = _require_session(request, sessions)
        await workflow.submit(session, body.text, str(body.request_id))
        return state_response(session)

    @application.post("/clarifications", status_code=status.HTTP_202_ACCEPTED)
    async def answer_clarification(body: ReplyBody, request: Request) -> dict[str, object]:
        session = _require_session(request, sessions)
        await workflow.answer(session, str(body.workflow_id), body.turn, body.text)
        return state_response(session)

    @application.post("/clarifications/cancel", status_code=status.HTTP_202_ACCEPTED)
    async def cancel_clarification(body: CancelBody, request: Request) -> dict[str, str]:
        session = _require_session(request, sessions)
        await workflow.cancel(session, str(body.workflow_id), body.turn)
        return {"status": "cancelled"}

    return application


def _require_session(request: Request, sessions: SessionStore) -> BrowserSession:
    session = sessions.get(request.cookies.get(_SESSION_COOKIE))
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Open the UCS page before sending a request",
        )
    return session
