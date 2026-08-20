import builtins
import importlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

import agent_evidence.cli as cli_module
from agent_evidence.api import create_app
from agent_evidence.cli import main
from agent_evidence.client import RecorderClient
from agent_evidence.models import (
    ActionType,
    AuditRecord,
    Outcome,
    RecordPhase,
    TrustLevel,
)
from agent_evidence.verify import verify_file

langchain_tools = pytest.importorskip("langchain_core.tools")
langgraph_graph = pytest.importorskip("langgraph.graph")
integration = importlib.import_module("agent_evidence.integrations.langgraph")
tool = langchain_tools.tool
StateGraph = langgraph_graph.StateGraph
LangGraphAuditCallback = integration.LangGraphAuditCallback
LangGraphAuditError = integration.LangGraphAuditError
LangGraphAuditSession = integration.LangGraphAuditSession
_payload_hash = integration._payload_hash
run_deterministic_demo = integration.run_deterministic_demo


class FakeClient:
    def __init__(self):
        self.session_id = uuid4()
        self.start_calls = []
        self.records = []
        self.close_calls = []
        self.fail_record = False
        self.fail_close = False
        self.fail_start = False

    def start_session(self, agent_id, agent_version, trust_level):
        self.start_calls.append((agent_id, agent_version, trust_level))
        if self.fail_start:
            raise RuntimeError("unavailable")
        return self.session_id

    def record(self, session_id, action_type, action_detail, outcome, record_phase):
        if self.fail_record:
            raise RuntimeError("unavailable")
        self.records.append(
            (session_id, action_type, action_detail, outcome, record_phase)
        )
        return SimpleNamespace(record_id=uuid4())

    def close_session(self, session_id):
        self.close_calls.append(session_id)
        if self.fail_close:
            raise RuntimeError("unavailable")
        return SimpleNamespace()


def test_payload_hash_is_deterministic_and_private():
    assert _payload_hash({"b": 2, "a": [True, None]}) == _payload_hash(
        {"a": [True, None], "b": 2}
    )
    assert _payload_hash({"nested": [1, {"x": "y"}]}) == _payload_hash(
        {"nested": [1, {"x": "y"}]}
    )
    assert _payload_hash("one") != _payload_hash("two")
    assert _payload_hash(float("nan")) == _payload_hash(float("inf"))

    class SecretObject:
        def __repr__(self):
            raise AssertionError("repr called")

        def __str__(self):
            raise AssertionError("str called")

    assert len(_payload_hash(SecretObject())) == 64


def test_tool_start_and_end_map_minimal_hashed_records():
    client = FakeClient()
    callback = LangGraphAuditCallback(client, client.session_id)
    run_id = uuid4()
    callback.on_tool_start(
        {"name": "calculator"},
        "raw-input-secret",
        run_id=run_id,
        inputs={"value": "raw-parameter-secret"},
    )
    call = client.records[0]
    assert call[1:] == (
        ActionType.TOOL_CALL,
        {
            "tool_name": "calculator",
            "parameters_hash": _payload_hash({"value": "raw-parameter-secret"}),
        },
        Outcome.SUCCESS,
        RecordPhase.PRE_EXECUTION,
    )
    call_id = callback._correlations[run_id][1]
    callback.on_tool_end("raw-output-secret", run_id=run_id)
    response = client.records[1]
    assert response[1] is ActionType.TOOL_RESPONSE
    assert response[2] == {
        "tool_name": "calculator",
        "response_hash": _payload_hash("raw-output-secret"),
        "parent_call_id": str(call_id),
    }
    assert str(run_id) not in str(response[2])
    assert "raw" not in json.dumps([call[2], response[2]])
    assert run_id not in callback._correlations


def test_tool_error_hashes_message_and_clears_state():
    client = FakeClient()
    callback = LangGraphAuditCallback(client, client.session_id)
    run_id = uuid4()
    callback.on_tool_start({}, "input", run_id=run_id, name="fallback")
    call_id = callback._correlations[run_id][1]
    callback.on_tool_error(ValueError("raw-error-secret"), run_id=run_id)
    error = client.records[-1]
    assert error[1:] == (
        ActionType.ERROR,
        {
            "error_type": "ValueError",
            "message_hash": _payload_hash("raw-error-secret"),
            "parent_call_id": str(call_id),
        },
        Outcome.FAILURE,
        RecordPhase.POST_EXECUTION,
    )
    assert "raw-error-secret" not in json.dumps(error[2])
    assert run_id not in callback._correlations


def test_mapping_errors_and_recorder_failure_are_safe():
    client = FakeClient()
    callback = LangGraphAuditCallback(client, client.session_id)
    run_id = uuid4()
    callback.on_tool_start({"name": "x"}, "input", run_id=run_id)
    with pytest.raises(LangGraphAuditError) as duplicate:
        callback.on_tool_start({"name": "x"}, "input", run_id=run_id)
    assert duplicate.value.code == "DUPLICATE_TOOL_START"
    for terminal in (
        lambda: callback.on_tool_end("x", run_id=uuid4()),
        lambda: callback.on_tool_error(ValueError("x"), run_id=uuid4()),
    ):
        with pytest.raises(LangGraphAuditError) as missing:
            terminal()
        assert missing.value.code == "TOOL_START_MISSING"

    failing = FakeClient()
    failing.fail_record = True
    failed_callback = LangGraphAuditCallback(failing, failing.session_id)
    with pytest.raises(LangGraphAuditError) as unavailable:
        failed_callback.on_tool_start({"name": "x"}, "secret", run_id=run_id)
    assert unavailable.value.code == "RECORDER_UNAVAILABLE"
    assert not failed_callback._correlations
    assert "secret" not in str(unavailable.value)


def test_concurrent_callbacks_and_unsupported_callbacks():
    client = FakeClient()
    callback = LangGraphAuditCallback(client, client.session_id)
    run_ids = [uuid4() for _ in range(20)]

    def complete(run_id):
        callback.on_tool_start({"name": "x"}, "input", run_id=run_id)
        callback.on_tool_end("output", run_id=run_id)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(complete, run_ids))
    assert len(client.records) == 40
    assert not callback._correlations
    before = len(client.records)
    callback.on_llm_start({}, ["secret"], run_id=uuid4())
    callback.on_chain_start({}, {"secret": True}, run_id=uuid4())
    callback.on_chain_end({"secret": True}, run_id=uuid4())
    callback.on_chain_error(ValueError("secret"), run_id=uuid4())
    assert len(client.records) == before


def test_audit_session_lifecycle_and_reuse():
    client = FakeClient()
    audit = LangGraphAuditSession(
        client, "https://example.com/a", "1.0.0", TrustLevel.L2
    )
    with pytest.raises(LangGraphAuditError):
        _ = audit.callback
    with audit as entered:
        assert entered.session_id == client.session_id
        assert entered.callback.raise_error is True
    assert len(client.start_calls) == len(client.close_calls) == 1
    with pytest.raises(LangGraphAuditError) as reused:
        with audit:
            pass
    assert reused.value.code == "SESSION_CLOSED"
    with pytest.raises(LangGraphAuditError) as closed_callback:
        audit.callback.on_tool_end("x", run_id=uuid4())
    assert closed_callback.value.code == "SESSION_CLOSED"

    close_failure = FakeClient()
    close_failure.fail_close = True
    with pytest.raises(LangGraphAuditError) as failed_close:
        with LangGraphAuditSession(
            close_failure, "https://example.com/a", "1.0.0", TrustLevel.L2
        ):
            pass
    assert failed_close.value.code == "SESSION_CLOSE_FAILED"


def test_graph_exception_is_recorded_closed_and_preserved():
    client = FakeClient()
    with pytest.raises(ValueError, match="raw-graph-secret"):
        with LangGraphAuditSession(
            client, "https://example.com/a", "1.0.0", TrustLevel.L2
        ):
            raise ValueError("raw-graph-secret")
    assert len(client.records) == 1
    detail = client.records[0][2]
    assert detail == {
        "error_type": "ValueError",
        "message_hash": _payload_hash("raw-graph-secret"),
    }
    assert "raw-graph-secret" not in json.dumps(detail)
    assert client.close_calls == [client.session_id]


def test_audit_failure_does_not_replace_graph_exception():
    client = FakeClient()
    client.fail_record = True
    client.fail_close = True
    with pytest.raises(KeyError, match="graph-secret") as original:
        with LangGraphAuditSession(
            client, "https://example.com/a", "1.0.0", TrustLevel.L2
        ):
            raise KeyError("graph-secret")
    assert any("Audit failure" in note for note in original.value.__notes__)
    client.fail_start = True
    with pytest.raises(LangGraphAuditError) as entry:
        with LangGraphAuditSession(
            client, "https://example.com/a", "1.0.0", TrustLevel.L2
        ):
            pass
    assert entry.value.code == "RECORDER_UNAVAILABLE"


def _real_client(data_dir):
    test_client = TestClient(create_app(data_dir))
    test_client.__enter__()
    client = RecorderClient()
    client._client.close()
    client._client = test_client
    return client, test_client


def test_real_graph_creates_private_valid_signed_trail(tmp_path):
    client, test_client = _real_client(tmp_path)
    try:
        result, session_id = run_deterministic_demo(client)
    finally:
        client.close()
        test_client.__exit__(None, None, None)
    expected_result = 987654321 * 314159
    assert result == {"value": 987654321, "result": expected_result}
    trail = tmp_path / "trails" / f"{session_id}.jsonl"
    records = [
        AuditRecord.model_validate_json(line) for line in trail.read_text().splitlines()
    ]
    assert [record.action_type for record in records] == [
        ActionType.LIFECYCLE,
        ActionType.TOOL_CALL,
        ActionType.TOOL_RESPONSE,
        ActionType.LIFECYCLE,
    ]
    assert verify_file(trail, tmp_path / "public_key.pem").valid
    trail_bytes = trail.read_bytes()
    assert b"987654321" not in trail_bytes
    assert str(expected_result).encode() not in trail_bytes
    data = [json.loads(line) for line in trail.read_text().splitlines()]
    data[1]["action_detail"]["tool_name"] = "edited"
    trail.write_text("\n".join(map(json.dumps, data)) + "\n")
    assert not verify_file(trail, tmp_path / "public_key.pem").valid


def test_real_failing_tool_preserves_error_and_hides_message(tmp_path):
    client, test_client = _real_client(tmp_path)

    @tool
    def fail_tool(value: int) -> int:
        """Fail deterministically."""
        raise RuntimeError(f"raw-failure-secret-{value}")

    def node(state, config):
        return {"value": fail_tool.invoke({"value": state["value"]}, config=config)}

    builder = StateGraph(dict)
    builder.add_node("fail", node)
    builder.set_entry_point("fail")
    builder.set_finish_point("fail")
    graph = builder.compile()
    audit = LangGraphAuditSession(
        client, "https://example.com/a", "1.0.0", TrustLevel.L2
    )
    try:
        with pytest.raises(RuntimeError, match="raw-failure-secret-7"):
            with audit:
                graph.invoke({"value": 7}, config={"callbacks": [audit.callback]})
    finally:
        client.close()
        test_client.__exit__(None, None, None)
    trail = tmp_path / "trails" / f"{audit.session_id}.jsonl"
    records = [
        AuditRecord.model_validate_json(line) for line in trail.read_text().splitlines()
    ]
    assert sum(record.action_type is ActionType.ERROR for record in records) == 2
    assert b"raw-failure-secret" not in trail.read_bytes()
    assert verify_file(trail, tmp_path / "public_key.pem").valid


def test_cli_help_missing_extra_and_core_import(tmp_path, capsys, monkeypatch):
    with pytest.raises(SystemExit) as help_exit:
        main(["langgraph-demo", "--help"])
    assert help_exit.value.code == 0

    real_import = builtins.__import__

    def missing_import(name, *args, **kwargs):
        if name == "agent_evidence.integrations.langgraph":
            raise ModuleNotFoundError("No module named 'langgraph'", name="langgraph")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing_import)
    assert main(["langgraph-demo"]) == 2
    assert "uv sync --extra langgraph" in capsys.readouterr().err
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys, agent_evidence; assert 'langgraph' not in sys.modules",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_cli_live_demo_and_unavailable_recorder(tmp_path, capsys, monkeypatch):
    client, test_client = _real_client(tmp_path)
    monkeypatch.setattr(cli_module, "RecorderClient", lambda _url: client)
    try:
        assert main(["langgraph-demo", "--url", "http://test"]) == 0
    finally:
        test_client.__exit__(None, None, None)
    output = capsys.readouterr().out
    session_id = output.split("Session: ", 1)[1].splitlines()[0]
    trail = tmp_path / "trails" / f"{session_id}.jsonl"
    assert verify_file(trail, tmp_path / "public_key.pem").valid

    unavailable = FakeClient()
    unavailable.fail_record = True
    unavailable.close = lambda: None
    monkeypatch.setattr(cli_module, "RecorderClient", lambda _url: unavailable)
    assert main(["langgraph-demo", "--url", "http://test"]) == 2
    error_output = capsys.readouterr().err
    assert "RECORDER_UNAVAILABLE" in error_output
    assert "987654321" not in error_output
    assert "Traceback" not in error_output
