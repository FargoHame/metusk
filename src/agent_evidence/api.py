"""Loopback recorder HTTP API."""

from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import AnyUrl, BaseModel, ConfigDict

from agent_evidence.models import (
    ActionType,
    JSONValue,
    Outcome,
    RecordPhase,
    TrustLevel,
)
from agent_evidence.recorder import IndependentRecorder, RecorderError


class _StartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: AnyUrl
    agent_version: str
    trust_level: TrustLevel


class _AppendRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: ActionType
    action_detail: dict[str, JSONValue]
    outcome: Outcome
    record_phase: RecordPhase


def _error(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"error": {"code": code, "message": message}}
    )


def create_app(data_dir: Path) -> FastAPI:
    recorder = IndependentRecorder(data_dir)
    app = FastAPI()
    app.state.recorder = recorder

    @app.exception_handler(RequestValidationError)
    def invalid_request(
        _request: Request, _exc: RequestValidationError
    ) -> JSONResponse:
        return _error("RECORD_INVALID", "Request is invalid", 422)

    @app.exception_handler(RecorderError)
    def recorder_error(_request: Request, exc: RecorderError) -> JSONResponse:
        status = {
            "SESSION_NOT_FOUND": 404,
            "SESSION_NOT_ACTIVE": 409,
            "SESSION_CLOSED": 409,
            "RECORD_INVALID": 422,
        }.get(exc.code, 500)
        return _error(exc.code, exc.message, status)

    @app.exception_handler(Exception)
    def internal_error(_request: Request, _exc: Exception) -> JSONResponse:
        return _error("RECORDER_ERROR", "Recorder operation failed", 500)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/public-key")
    def public_key() -> Response:
        return Response(recorder.public_key_pem(), media_type="text/plain")

    @app.post("/v1/sessions", status_code=201)
    def start_session(request: _StartRequest) -> dict[str, object]:
        record = recorder.start_session(
            str(request.agent_id), request.agent_version, request.trust_level
        )
        return {
            "session_id": str(record.session_id),
            "record": record.json_compatible(),
        }

    @app.post("/v1/sessions/{session_id}/records", status_code=201)
    def append(session_id: UUID, request: _AppendRequest) -> dict[str, object]:
        record = recorder.record(
            session_id,
            request.action_type,
            request.action_detail,
            request.outcome,
            request.record_phase,
        )
        return {"record": record.json_compatible()}

    @app.post("/v1/sessions/{session_id}/close", status_code=201)
    def close(session_id: UUID) -> dict[str, object]:
        return {"record": recorder.close_session(session_id).json_compatible()}

    return app
