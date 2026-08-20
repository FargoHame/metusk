"""Synchronous client for the independent recorder."""

from uuid import UUID

import httpx

from agent_evidence.models import (
    ActionType,
    AuditRecord,
    JSONValue,
    Outcome,
    RecordPhase,
    TrustLevel,
)


class RecorderClientError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"HTTP {status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class RecorderClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8765",
        timeout: float = 10.0,
    ) -> None:
        self._client = httpx.Client(base_url=base_url, timeout=timeout)

    def _request(self, method: str, path: str, **kwargs: object) -> httpx.Response:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise RecorderClientError(0, "CLIENT_ERROR", str(exc)) from exc
        if response.is_success:
            return response
        try:
            error = response.json()["error"]
            code = str(error["code"])
            message = str(error["message"])
        except (ValueError, KeyError, TypeError):
            code = "RECORDER_ERROR"
            message = "Recorder returned an invalid error response"
        raise RecorderClientError(response.status_code, code, message)

    def start_session(
        self,
        agent_id: str,
        agent_version: str,
        trust_level: TrustLevel,
    ) -> UUID:
        response = self._request(
            "POST",
            "/v1/sessions",
            json={
                "agent_id": agent_id,
                "agent_version": agent_version,
                "trust_level": trust_level.value,
            },
        )
        return UUID(response.json()["session_id"])

    def record(
        self,
        session_id: UUID,
        action_type: ActionType,
        action_detail: dict[str, JSONValue],
        outcome: Outcome,
        record_phase: RecordPhase,
    ) -> AuditRecord:
        response = self._request(
            "POST",
            f"/v1/sessions/{session_id}/records",
            json={
                "action_type": action_type.value,
                "action_detail": action_detail,
                "outcome": outcome.value,
                "record_phase": record_phase.value,
            },
        )
        return AuditRecord.model_validate(response.json()["record"])

    def close_session(self, session_id: UUID) -> AuditRecord:
        response = self._request("POST", f"/v1/sessions/{session_id}/close")
        return AuditRecord.model_validate(response.json()["record"])

    def get_public_key(self) -> bytes:
        return self._request("GET", "/v1/public-key").content

    def close(self) -> None:
        self._client.close()
