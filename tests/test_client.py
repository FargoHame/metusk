from uuid import uuid4

import httpx
import pytest

from agent_evidence.client import RecorderClient, RecorderClientError
from agent_evidence.models import ActionType, Outcome, RecordPhase, TrustLevel
from agent_evidence.recorder import IndependentRecorder


def test_client_flow_timeout_parsing_and_close(tmp_path, monkeypatch):
    recorder = IndependentRecorder(tmp_path)
    genesis = recorder.start_session("https://example.com/a", "1.0.0", TrustLevel.L2)
    decision = recorder.record(
        genesis.session_id,
        ActionType.DECISION,
        {"decision_type": "route"},
        Outcome.SUCCESS,
        RecordPhase.CONCURRENT,
    )
    final = recorder.close_session(genesis.session_id)
    responses = iter(
        [
            httpx.Response(
                201,
                json={
                    "session_id": str(genesis.session_id),
                    "record": genesis.json_compatible(),
                },
            ),
            httpx.Response(201, json={"record": decision.json_compatible()}),
            httpx.Response(201, json={"record": final.json_compatible()}),
            httpx.Response(200, content=recorder.public_key_pem()),
        ]
    )
    observed = {}

    def handler(request):
        observed["timeout"] = request.extensions["timeout"]["read"]
        return next(responses)

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client(transport=transport, base_url="http://test", timeout=3.5)
    monkeypatch.setattr(httpx, "Client", lambda **_kwargs: real_client)
    client = RecorderClient(timeout=3.5)
    assert (
        client.start_session("https://example.com/a", "1.0.0", TrustLevel.L2)
        == genesis.session_id
    )
    parsed = client.record(
        genesis.session_id,
        ActionType.DECISION,
        {"decision_type": "route"},
        Outcome.SUCCESS,
        RecordPhase.CONCURRENT,
    )
    assert parsed == decision
    assert client.close_session(genesis.session_id) == final
    assert client.get_public_key() == recorder.public_key_pem()
    assert observed["timeout"] == 3.5
    client.close()
    assert real_client.is_closed


def test_client_converts_recorder_error(monkeypatch):
    transport = httpx.MockTransport(
        lambda _request: httpx.Response(
            404,
            json={
                "error": {
                    "code": "SESSION_NOT_FOUND",
                    "message": "Session does not exist",
                }
            },
        )
    )
    real_client = httpx.Client(transport=transport, base_url="http://test")
    monkeypatch.setattr(httpx, "Client", lambda **_kwargs: real_client)
    client = RecorderClient()
    with pytest.raises(RecorderClientError) as error:
        client.close_session(uuid4())
    assert (error.value.status, error.value.code) == (404, "SESSION_NOT_FOUND")
    client.close()
