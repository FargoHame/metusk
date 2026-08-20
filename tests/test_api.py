from fastapi.testclient import TestClient

from agent_evidence.api import create_app


def start(client):
    return client.post(
        "/v1/sessions",
        json={
            "agent_id": "https://example.com/a",
            "agent_version": "1.0.0",
            "trust_level": "L2",
        },
    )


def test_health_public_key_and_complete_flow(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        assert client.get("/health").json() == {"status": "ok"}
        key = client.get("/v1/public-key")
        assert key.headers["content-type"].startswith("text/plain")
        assert b"BEGIN PUBLIC KEY" in key.content
        created = start(client)
        assert created.status_code == 201
        session_id = created.json()["session_id"]
        appended = client.post(
            f"/v1/sessions/{session_id}/records",
            json={
                "action_type": "decision",
                "action_detail": {"decision_type": "route"},
                "outcome": "success",
                "record_phase": "concurrent",
            },
        )
        assert appended.status_code == 201
        closed = client.post(f"/v1/sessions/{session_id}/close")
        assert closed.status_code == 201
        again = client.post(f"/v1/sessions/{session_id}/close")
        assert again.status_code == 409
        assert again.json()["error"]["code"] == "SESSION_CLOSED"


def test_invalid_unknown_and_inactive_errors_are_sanitized(tmp_path):
    with TestClient(create_app(tmp_path)) as client:
        invalid = client.post("/v1/sessions", json={"agent_id": "bad"})
        assert invalid.status_code == 422
        assert invalid.json()["error"]["code"] == "RECORD_INVALID"
        unknown = client.post("/v1/sessions/00000000-0000-4000-8000-000000000000/close")
        assert unknown.status_code == 404
        body = unknown.json()
        assert body["error"]["code"] == "SESSION_NOT_FOUND"
        assert str(tmp_path) not in str(body) and "traceback" not in str(body).lower()
        session_id = start(client).json()["session_id"]
    with TestClient(create_app(tmp_path)) as restarted:
        inactive = restarted.post(f"/v1/sessions/{session_id}/close")
        assert inactive.status_code == 409
        assert inactive.json()["error"]["code"] == "SESSION_NOT_ACTIVE"
