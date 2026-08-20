import json
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from agent_evidence.canonical import record_hash
from agent_evidence.models import (
    ActionType,
    AuditRecord,
    Outcome,
    RecordPhase,
    TrustLevel,
)
from agent_evidence.recorder import IndependentRecorder, RecorderError
from agent_evidence.signing import verify_record_signature
from agent_evidence.verify import verify_file


def test_keys_persist_and_are_not_overwritten(tmp_path):
    first = IndependentRecorder(tmp_path)
    private = (tmp_path / "private_key.pem").read_bytes()
    component = first.component_uri
    second = IndependentRecorder(tmp_path)
    assert (tmp_path / "private_key.pem").read_bytes() == private
    assert second.component_uri == component


def test_malformed_existing_key_fails(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "private_key.pem").write_text("not a key")
    with pytest.raises(ValueError, match="malformed"):
        IndependentRecorder(tmp_path)


def test_signed_chain_and_trail_filename(tmp_path):
    recorder = IndependentRecorder(tmp_path)
    genesis = recorder.start_session("https://example.com/a", "1.0.0", TrustLevel.L2)
    middle = recorder.record(
        genesis.session_id,
        ActionType.DECISION,
        {"decision_type": "route"},
        Outcome.SUCCESS,
        RecordPhase.CONCURRENT,
    )
    final = recorder.close_session(genesis.session_id)
    assert middle.parent_record_id == genesis.record_id
    assert middle.prev_hash == record_hash(genesis)
    assert final.prev_hash == record_hash(middle)
    records = [
        AuditRecord.model_validate_json(line)
        for line in (tmp_path / "trails" / f"{genesis.session_id}.jsonl")
        .read_text()
        .splitlines()
    ]
    assert all(
        record.recording_component == genesis.recording_component for record in records
    )
    assert all(
        verify_record_signature(record, recorder.public_key_pem()) for record in records
    )


def test_concurrent_appends_do_not_branch(tmp_path):
    recorder = IndependentRecorder(tmp_path)
    genesis = recorder.start_session("https://example.com/a", "1.0.0", TrustLevel.L2)

    def append(number):
        return recorder.record(
            genesis.session_id,
            ActionType.DECISION,
            {"decision_type": f"route-{number}"},
            Outcome.SUCCESS,
            RecordPhase.CONCURRENT,
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(append, range(20)))
    recorder.close_session(genesis.session_id)
    data = [
        AuditRecord.model_validate(item)
        for item in map(
            json.loads,
            (tmp_path / "trails" / f"{genesis.session_id}.jsonl")
            .read_text()
            .splitlines(),
        )
    ]
    for previous, current in zip(data, data[1:], strict=False):
        assert current.parent_record_id == previous.record_id
        assert current.prev_hash == record_hash(previous)


def test_closed_inactive_and_unknown_sessions(tmp_path):
    recorder = IndependentRecorder(tmp_path)
    genesis = recorder.start_session("https://example.com/a", "1.0.0", TrustLevel.L2)
    recorder.close_session(genesis.session_id)
    with pytest.raises(RecorderError, match="closed") as closed:
        recorder.close_session(genesis.session_id)
    assert closed.value.code == "SESSION_CLOSED"
    restarted = IndependentRecorder(tmp_path)
    with pytest.raises(RecorderError) as inactive:
        restarted.close_session(genesis.session_id)
    assert inactive.value.code == "SESSION_NOT_ACTIVE"
    with pytest.raises(RecorderError) as unknown:
        restarted.close_session(uuid4())
    assert unknown.value.code == "SESSION_NOT_FOUND"


def test_signed_verification_and_tampering(tmp_path):
    recorder = IndependentRecorder(tmp_path / "data")
    genesis = recorder.start_session("https://example.com/a", "1.0.0", TrustLevel.L2)
    recorder.record(
        genesis.session_id,
        ActionType.DECISION,
        {"decision_type": "route"},
        Outcome.SUCCESS,
        RecordPhase.CONCURRENT,
    )
    recorder.close_session(genesis.session_id)
    trail = tmp_path / "data" / "trails" / f"{genesis.session_id}.jsonl"
    key = tmp_path / "data" / "public_key.pem"
    assert verify_file(trail, key).valid
    assert "SIGNATURE_KEY_REQUIRED" in {
        error.code for error in verify_file(trail).errors
    }
    wrong_key = tmp_path / "wrong.pem"
    wrong_key.write_bytes(IndependentRecorder(tmp_path / "other").public_key_pem())
    wrong_codes = {error.code for error in verify_file(trail, wrong_key).errors}
    assert {"RECORDING_COMPONENT_MISMATCH", "SIGNATURE_INVALID"} <= wrong_codes

    original = trail.read_text()
    data = [json.loads(line) for line in original.splitlines()]
    data[1]["action_detail"]["decision_type"] = "edited"
    trail.write_text("\n".join(map(json.dumps, data)) + "\n")
    assert "SIGNATURE_INVALID" in {
        error.code for error in verify_file(trail, key).errors
    }

    trail.write_text(original)
    data = [json.loads(line) for line in original.splitlines()]
    data[1].pop("signature")
    trail.write_text("\n".join(map(json.dumps, data)) + "\n")
    assert "SIGNATURE_MISSING" in {
        error.code for error in verify_file(trail, key).errors
    }

    trail.write_text(original)
    data = [json.loads(line) for line in original.splitlines()]
    data[1]["recording_component"] = "urn:agent-evidence:recorder:" + "0" * 64
    data[2]["prev_hash"] = "0" * 64
    trail.write_text("\n".join(map(json.dumps, data)) + "\n")
    codes = {error.code for error in verify_file(trail, key).errors}
    assert {"RECORDING_COMPONENT_MISMATCH", "HASH_MISMATCH"} <= codes
