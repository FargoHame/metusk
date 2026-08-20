import json

import pytest

from agent_evidence.canonical import record_hash
from agent_evidence.models import (
    ActionType,
    AuditRecord,
    Outcome,
    RecordPhase,
    TrustLevel,
)
from agent_evidence.session import AuditSession


def test_session_lifecycle_and_links(tmp_path):
    path = tmp_path / "trail.jsonl"
    session = AuditSession.start("https://example.com/a", "1.0.0", TrustLevel.L1, path)
    genesis = session._previous
    middle = session.record(
        ActionType.DECISION,
        {"decision_type": "route"},
        Outcome.SUCCESS,
        RecordPhase.CONCURRENT,
    )
    final = session.close()
    records = [
        AuditRecord.model_validate_json(line) for line in path.read_text().splitlines()
    ]
    assert records[0].action_detail == {"event": "session_start"}
    assert middle.parent_record_id == genesis.record_id
    assert middle.prev_hash == record_hash(genesis)
    assert final.action_detail == {"event": "session_end"}
    assert len({record.session_id for record in records}) == 1
    assert len(records) == len(path.read_text().splitlines()) == 3
    assert all(json.loads(line) for line in path.read_text().splitlines())


def test_double_close_and_record_after_close(tmp_path):
    session = AuditSession.start(
        "https://example.com/a", "1.0.0", TrustLevel.L1, tmp_path / "x"
    )
    session.close()
    with pytest.raises(RuntimeError):
        session.close()
    with pytest.raises(RuntimeError):
        session.record(
            ActionType.ERROR, {"message": "x"}, Outcome.FAILURE, RecordPhase.CONCURRENT
        )
