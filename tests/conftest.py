from datetime import UTC, datetime
from uuid import uuid4

import pytest

from agent_evidence.models import AuditRecord


@pytest.fixture
def record_data():
    return {
        "record_id": uuid4(),
        "timestamp": datetime(2026, 1, 1, tzinfo=UTC),
        "agent_id": "https://example.com/agent",
        "agent_version": "1.2.3",
        "session_id": uuid4(),
        "action_type": "decision",
        "action_detail": {"decision_type": "route", "nested": {"b": 2, "a": 1}},
        "outcome": "success",
        "trust_level": "L2",
        "parent_record_id": uuid4(),
        "prev_hash": "a" * 64,
        "record_phase": "concurrent",
    }


@pytest.fixture
def record(record_data):
    return AuditRecord.model_validate(record_data)
