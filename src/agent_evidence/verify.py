"""Verification of JSONL audit trails."""

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from pydantic import ValidationError

from agent_evidence.canonical import record_hash
from agent_evidence.jsonl import MAX_RECORD_BYTES
from agent_evidence.models import ActionType, AuditRecord, Outcome, RecordPhase
from agent_evidence.signing import (
    component_uri_from_public_key,
    verify_record_signature,
)


@dataclass(frozen=True)
class VerificationError:
    code: str
    record_index: int | None
    message: str


@dataclass(frozen=True)
class VerificationReport:
    valid: bool
    record_count: int
    session_id: str | None
    first_integrity_break: int | None
    errors: list[VerificationError]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def verify_file(path: Path, public_key: Path | None = None) -> VerificationReport:
    """Verify a trail; all record indexes in results are zero-based."""
    errors: list[VerificationError] = []
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        return _report(
            0, None, None, [VerificationError("FILE_INVALID_UTF8", None, str(exc))]
        )

    raw_lines = [line for line in text.splitlines() if line.strip()]
    records: list[AuditRecord | None] = []
    raw_objects: list[object | None] = []
    invalid_signature_indexes: set[int] = set()
    for index, line in enumerate(raw_lines):
        if len(line.encode("utf-8")) > MAX_RECORD_BYTES:
            errors.append(
                VerificationError("RECORD_TOO_LARGE", index, "record exceeds 256 KiB")
            )
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(VerificationError("JSON_INVALID", index, str(exc)))
            records.append(None)
            raw_objects.append(None)
            continue
        raw_objects.append(data)
        try:
            records.append(AuditRecord.model_validate(data))
        except ValidationError as exc:
            if any(error["loc"] == ("signature",) for error in exc.errors()):
                errors.append(
                    VerificationError(
                        "SIGNATURE_INVALID", index, "signature encoding is invalid"
                    )
                )
                invalid_signature_indexes.add(index)
            else:
                errors.append(VerificationError("RECORD_INVALID", index, str(exc)))
            records.append(None)

    if len(raw_lines) < 2:
        errors.append(
            VerificationError(
                "TRAIL_TOO_SHORT", None, "trail must contain at least two records"
            )
        )

    valid_records = [
        (index, record) for index, record in enumerate(records) if record is not None
    ]
    session_id = str(valid_records[0][1].session_id) if valid_records else None
    first_break: int | None = (
        min(invalid_signature_indexes) if invalid_signature_indexes else None
    )
    seen: set[object] = set()
    for index, record in valid_records:
        if str(record.session_id) != session_id:
            errors.append(
                VerificationError(
                    "SESSION_MISMATCH",
                    index,
                    "session_id differs from the first record",
                )
            )
        if record.record_id in seen:
            errors.append(
                VerificationError(
                    "DUPLICATE_RECORD_ID", index, "record_id is not unique"
                )
            )
        seen.add(record.record_id)

    if records and records[0] is not None:
        genesis = records[0]
        if not (
            genesis.action_type is ActionType.LIFECYCLE
            and genesis.action_detail == {"event": "session_start"}
            and genesis.outcome is Outcome.SUCCESS
            and genesis.record_phase is RecordPhase.CONCURRENT
            and genesis.parent_record_id is None
            and genesis.prev_hash is None
        ):
            errors.append(
                VerificationError(
                    "GENESIS_INVALID",
                    0,
                    "record zero is not a valid session_start genesis record",
                )
            )
    elif records:
        errors.append(
            VerificationError("GENESIS_INVALID", 0, "record zero is not a valid record")
        )

    hash_checks_enabled = True
    for index in range(1, len(records)):
        current, previous = records[index], records[index - 1]
        if current is None or previous is None:
            continue
        if current.timestamp < previous.timestamp:
            errors.append(
                VerificationError(
                    "TIMESTAMP_REGRESSION",
                    index,
                    "timestamp is earlier than the previous record",
                )
            )
        if current.parent_record_id is None or current.prev_hash is None:
            errors.append(
                VerificationError(
                    "CHAIN_FIELDS_MISSING",
                    index,
                    "non-genesis record has null chain fields",
                )
            )
        if current.parent_record_id != previous.record_id:
            errors.append(
                VerificationError(
                    "PARENT_MISMATCH",
                    index,
                    "parent_record_id does not equal the previous record_id",
                )
            )
            if first_break is None:
                first_break = index
        if hash_checks_enabled and current.prev_hash != record_hash(previous):
            errors.append(
                VerificationError(
                    "HASH_MISMATCH",
                    index,
                    "prev_hash does not match the previous record",
                )
            )
            if first_break is None:
                first_break = index
            hash_checks_enabled = False

    end_indexes = [
        index
        for index, record in valid_records
        if record.action_type is ActionType.LIFECYCLE
        and record.action_detail.get("event") == "session_end"
    ]
    final = records[-1] if records else None
    final_valid = final is not None and (
        final.action_type is ActionType.LIFECYCLE
        and final.action_detail == {"event": "session_end"}
        and final.outcome is Outcome.SUCCESS
        and final.record_phase is RecordPhase.POST_EXECUTION
    )
    if not final_valid or any(index != len(records) - 1 for index in end_indexes):
        errors.append(
            VerificationError(
                "SESSION_END_INVALID",
                len(records) - 1 if records else None,
                "only the final record may be a valid session_end",
            )
        )

    has_component = any(
        isinstance(item, dict) and item.get("recording_component") is not None
        for item in raw_objects
    )
    has_signature = any(
        isinstance(item, dict) and item.get("signature") is not None
        for item in raw_objects
    )
    signed_profile = has_component or has_signature
    components: list[tuple[int, str]] = []
    if has_component:
        for index, record in valid_records:
            if record.recording_component is None:
                errors.append(
                    VerificationError(
                        "RECORDING_COMPONENT_MISSING",
                        index,
                        "independently recorded trail record lacks recording_component",
                    )
                )
                if first_break is None or index < first_break:
                    first_break = index
            else:
                components.append((index, str(record.recording_component)))
        if components:
            expected_component = components[0][1]
            for index, component in components[1:]:
                if component != expected_component:
                    errors.append(
                        VerificationError(
                            "RECORDING_COMPONENT_MISMATCH",
                            index,
                            "recording_component differs within the trail",
                        )
                    )
                    if first_break is None or index < first_break:
                        first_break = index

    if signed_profile:
        for index, record in valid_records:
            if record.signature is None:
                errors.append(
                    VerificationError(
                        "SIGNATURE_MISSING",
                        index,
                        "signed trail record lacks signature",
                    )
                )
                if first_break is None or index < first_break:
                    first_break = index

        if public_key is None:
            errors.append(
                VerificationError(
                    "SIGNATURE_KEY_REQUIRED",
                    None,
                    "signed trail verification requires a public key",
                )
            )
        else:
            public_key_pem = public_key.read_bytes()
            try:
                key_component = component_uri_from_public_key(public_key_pem)
            except (ValueError, TypeError):
                errors.append(
                    VerificationError(
                        "SIGNATURE_INVALID", None, "public key is malformed or unusable"
                    )
                )
            else:
                for index, component in components:
                    if component != key_component:
                        errors.append(
                            VerificationError(
                                "RECORDING_COMPONENT_MISMATCH",
                                index,
                                "recording_component does not match the public key",
                            )
                        )
                        if first_break is None or index < first_break:
                            first_break = index
                        break
                for index, record in valid_records:
                    if (
                        record.signature is not None
                        and index not in invalid_signature_indexes
                        and not verify_record_signature(record, public_key_pem)
                    ):
                        errors.append(
                            VerificationError(
                                "SIGNATURE_INVALID",
                                index,
                                "record signature is invalid",
                            )
                        )
                        if first_break is None or index < first_break:
                            first_break = index

    return _report(len(raw_lines), session_id, first_break, errors)


def _report(
    count: int,
    session_id: str | None,
    first_break: int | None,
    errors: list[VerificationError],
) -> VerificationReport:
    return VerificationReport(not errors, count, session_id, first_break, errors)
