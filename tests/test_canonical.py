from agent_evidence.canonical import canonical_bytes, record_hash
from agent_evidence.models import AuditRecord


def test_key_order_does_not_matter(record_data):
    first = AuditRecord.model_validate(record_data)
    record_data["action_detail"] = {
        "nested": {"a": 1, "b": 2},
        "decision_type": "route",
    }
    second = AuditRecord.model_validate(record_data)
    assert canonical_bytes(first) == canonical_bytes(second)


def test_hash_is_repeatable(record):
    assert record_hash(record) == record_hash(record)


def test_nested_data_is_canonical(record):
    assert b'"nested":{"a":1,"b":2}' in canonical_bytes(record)


def test_field_change_changes_hash(record_data):
    first = AuditRecord.model_validate(record_data)
    record_data["outcome"] = "failure"
    assert record_hash(first) != record_hash(AuditRecord.model_validate(record_data))


def test_rfc8785_orders_object_keys(record):
    result = canonical_bytes(record)
    assert result.index(b'"action_detail"') < result.index(b'"action_type"')
