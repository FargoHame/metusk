"""Pinned draft audit record model."""

import math
import re
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import (
    UUID4,
    AnyUrl,
    BaseModel,
    ConfigDict,
    JsonValue,
    field_serializer,
    field_validator,
    model_validator,
)

JSONValue = JsonValue

_SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ActionType(StrEnum):
    TOOL_CALL = "tool_call"
    TOOL_RESPONSE = "tool_response"
    DECISION = "decision"
    DELEGATION = "delegation"
    ESCALATION = "escalation"
    ERROR = "error"
    LIFECYCLE = "lifecycle"


class Outcome(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    DENIED = "denied"
    ESCALATED = "escalated"


class TrustLevel(StrEnum):
    L0 = "L0"
    L1 = "L1"
    L2 = "L2"
    L3 = "L3"
    L4 = "L4"


class RecordPhase(StrEnum):
    PRE_EXECUTION = "pre_execution"
    POST_EXECUTION = "post_execution"
    CONCURRENT = "concurrent"


def _validate_json(value: Any) -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("action_detail must not contain NaN or Infinity")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json(item)
        return
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        for item in value.values():
            _validate_json(item)
        return
    raise ValueError("action_detail contains a value that is not representable in JSON")


class AuditRecord(BaseModel):
    """The complete minimal record profile for the pinned Internet-Draft."""

    model_config = ConfigDict(extra="forbid")

    record_id: UUID4
    timestamp: datetime
    agent_id: AnyUrl
    agent_version: str
    session_id: UUID4
    action_type: ActionType
    action_detail: dict[str, JSONValue]
    outcome: Outcome
    trust_level: TrustLevel
    parent_record_id: UUID4 | None
    prev_hash: str | None
    record_phase: RecordPhase

    @field_validator("timestamp")
    @classmethod
    def timestamp_has_offset(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must include a UTC offset")
        return value

    @field_validator("agent_version")
    @classmethod
    def semantic_version(cls, value: str) -> str:
        if not _SEMVER.fullmatch(value):
            raise ValueError("agent_version must be a semantic version")
        return value

    @field_validator("prev_hash")
    @classmethod
    def previous_hash(cls, value: str | None) -> str | None:
        if value is not None and not _SHA256.fullmatch(value):
            raise ValueError("prev_hash must be lowercase SHA-256 hexadecimal")
        return value

    @field_validator("action_detail")
    @classmethod
    def json_object(cls, value: dict[str, JSONValue]) -> dict[str, JSONValue]:
        if not value:
            raise ValueError("action_detail must be a non-empty object")
        _validate_json(value)
        return value

    @model_validator(mode="after")
    def action_requirements(self) -> "AuditRecord":
        detail = self.action_detail

        def nonempty_string(name: str) -> None:
            if not isinstance(detail.get(name), str) or not detail[name].strip():
                raise ValueError(f"{self.action_type.value} requires non-empty {name}")

        if self.action_type is ActionType.TOOL_CALL:
            nonempty_string("tool_name")
            value = detail.get("parameters_hash")
            if not isinstance(value, str) or not _SHA256.fullmatch(value):
                raise ValueError(
                    "tool_call requires a lowercase SHA-256 parameters_hash"
                )
        elif self.action_type is ActionType.TOOL_RESPONSE:
            nonempty_string("tool_name")
            value = detail.get("response_hash")
            if not isinstance(value, str) or not _SHA256.fullmatch(value):
                raise ValueError(
                    "tool_response requires a lowercase SHA-256 response_hash"
                )
            from uuid import UUID

            try:
                parent = UUID(str(detail.get("parent_call_id")), version=4)
            except (ValueError, TypeError, AttributeError) as exc:
                raise ValueError(
                    "tool_response requires a UUIDv4 parent_call_id"
                ) from exc
            if parent.version != 4 or str(parent) != detail.get("parent_call_id"):
                raise ValueError("tool_response requires a UUIDv4 parent_call_id")
        elif self.action_type is ActionType.DECISION:
            nonempty_string("decision_type")
        elif self.action_type is ActionType.LIFECYCLE:
            if detail.get("event") not in {"session_start", "session_end"}:
                raise ValueError("lifecycle event must be session_start or session_end")
        return self

    @field_serializer("timestamp")
    def serialize_timestamp(self, value: datetime) -> str:
        utc = value.astimezone(UTC)
        milliseconds = utc.microsecond // 1000
        return f"{utc:%Y-%m-%dT%H:%M:%S}.{milliseconds:03d}Z"

    @field_serializer("record_id", "session_id", "parent_record_id")
    def serialize_uuid(self, value: Any) -> str | None:
        return None if value is None else str(value)

    @field_serializer("agent_id")
    def serialize_uri(self, value: AnyUrl) -> str:
        return str(value)
