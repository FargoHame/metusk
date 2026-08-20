import json
from uuid import uuid4

import pytest

from agent_evidence.models import ActionType, Outcome, RecordPhase, TrustLevel
from agent_evidence.session import AuditSession
from agent_evidence.verify import verify_file


@pytest.fixture
def trail(tmp_path):
    path = tmp_path / "trail.jsonl"
    session = AuditSession.start("https://example.com/a", "1.0.0", TrustLevel.L1, path)
    session.record(
        ActionType.DECISION,
        {"decision_type": "route"},
        Outcome.SUCCESS,
        RecordPhase.CONCURRENT,
    )
    session.close()
    return path


def lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def write(path, data):
    path.write_text(
        "".join(json.dumps(item, separators=(",", ":")) + "\n" for item in data),
        encoding="utf-8",
    )


def codes(path):
    return {error.code for error in verify_file(path).errors}


def test_valid_trail(trail):
    report = verify_file(trail)
    assert report.valid and report.record_count == 3


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        (
            lambda data: data[1]["action_detail"].update(decision_type="changed"),
            "HASH_MISMATCH",
        ),
        (lambda data: data.pop(1), "PARENT_MISMATCH"),
        (lambda data: data.insert(1, data[1].copy()), "PARENT_MISMATCH"),
        (
            lambda data: data.__setitem__(slice(1, 3), reversed(data[1:3])),
            "SESSION_END_INVALID",
        ),
        (lambda data: data[1].update(parent_record_id=str(uuid4())), "PARENT_MISMATCH"),
        (lambda data: data[1].update(prev_hash="b" * 64), "HASH_MISMATCH"),
        (
            lambda data: data[1].update(record_id=data[0]["record_id"]),
            "DUPLICATE_RECORD_ID",
        ),
        (lambda data: data[1].update(session_id=str(uuid4())), "SESSION_MISMATCH"),
        (
            lambda data: data[1].update(timestamp="2020-01-01T00:00:00.000Z"),
            "TIMESTAMP_REGRESSION",
        ),
        (lambda data: data.pop(0), "GENESIS_INVALID"),
        (lambda data: data.pop(), "SESSION_END_INVALID"),
        (
            lambda data: data[1].update(
                action_type="lifecycle", action_detail={"event": "session_end"}
            ),
            "SESSION_END_INVALID",
        ),
    ],
)
def test_tampering(trail, mutation, expected):
    data = lines(trail)
    mutation(data)
    write(trail, data)
    assert expected in codes(trail)


def test_oversized_record(trail):
    data = lines(trail)
    data[1]["action_detail"]["padding"] = "x" * (256 * 1024)
    write(trail, data)
    assert "RECORD_TOO_LARGE" in codes(trail)


def test_malformed_json(trail):
    trail.write_text("{bad}\n", encoding="utf-8")
    assert "JSON_INVALID" in codes(trail)
