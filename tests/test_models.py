from datetime import datetime
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from agent_evidence.models import AuditRecord


def rejects(data):
    with pytest.raises(ValidationError):
        AuditRecord.model_validate(data)


def test_valid_record_serializes_profile_types(record):
    data = record.model_dump(mode="json")
    assert data["timestamp"].endswith(".000Z")
    assert isinstance(data["record_id"], str)
    assert data["agent_id"] == "https://example.com/agent"


@pytest.mark.parametrize("field", ["record_id", "action_type", "prev_hash"])
def test_missing_mandatory_field(record_data, field):
    record_data.pop(field)
    rejects(record_data)


def test_unknown_top_level_field(record_data):
    record_data["typo"] = True
    rejects(record_data)


def test_non_v4_uuid(record_data):
    record_data["record_id"] = UUID("00000000-0000-1000-8000-000000000000")
    rejects(record_data)


def test_naive_timestamp(record_data):
    record_data["timestamp"] = datetime(2026, 1, 1)
    rejects(record_data)


@pytest.mark.parametrize("value", ["1.2", "v1.2.3", "01.2.3"])
def test_invalid_semantic_version(record_data, value):
    record_data["agent_version"] = value
    rejects(record_data)


def test_invalid_enum(record_data):
    record_data["outcome"] = "maybe"
    rejects(record_data)


@pytest.mark.parametrize("value", ["A" * 64, "abc", "g" * 64])
def test_invalid_hash(record_data, value):
    record_data["prev_hash"] = value
    rejects(record_data)


def test_empty_action_detail(record_data):
    record_data["action_detail"] = {}
    rejects(record_data)


def test_invalid_tool_call_detail(record_data):
    record_data.update(action_type="tool_call", action_detail={"tool_name": "x"})
    rejects(record_data)


def test_invalid_tool_response_detail(record_data):
    record_data.update(
        action_type="tool_response",
        action_detail={
            "tool_name": "x",
            "response_hash": "b" * 64,
            "parent_call_id": str(uuid4()).upper(),
        },
    )
    rejects(record_data)


def test_non_json_action_detail(record_data):
    record_data["action_detail"] = {"decision_type": "x", "bad": {1, 2}}
    rejects(record_data)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_non_finite_number(record_data, value):
    record_data["action_detail"] = {"decision_type": "x", "bad": value}
    rejects(record_data)
