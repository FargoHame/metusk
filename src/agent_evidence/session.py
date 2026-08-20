"""Audit trail session construction."""

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from agent_evidence.canonical import record_hash
from agent_evidence.jsonl import JsonlSink
from agent_evidence.models import (
    ActionType,
    AuditRecord,
    JSONValue,
    Outcome,
    RecordPhase,
    TrustLevel,
)


class AuditSession:
    def __init__(
        self, agent_id: str, agent_version: str, trust_level: TrustLevel, output: Path
    ) -> None:
        self.agent_id = agent_id
        self.agent_version = agent_version
        self.trust_level = trust_level
        self.session_id = uuid4()
        self._sink = JsonlSink(output)
        self._previous: AuditRecord | None = None
        self._closed = False

    @classmethod
    def start(
        cls, agent_id: str, agent_version: str, trust_level: TrustLevel, output: Path
    ) -> "AuditSession":
        session = cls(agent_id, agent_version, trust_level, output)
        session._append(
            ActionType.LIFECYCLE,
            {"event": "session_start"},
            Outcome.SUCCESS,
            RecordPhase.CONCURRENT,
        )
        return session

    def _append(
        self,
        action_type: ActionType,
        action_detail: dict[str, JSONValue],
        outcome: Outcome,
        record_phase: RecordPhase,
    ) -> AuditRecord:
        now = datetime.now(UTC)
        previous = self._previous
        if previous is not None and now < previous.timestamp:
            now = previous.timestamp
        record = AuditRecord(
            record_id=uuid4(),
            timestamp=now,
            agent_id=self.agent_id,
            agent_version=self.agent_version,
            session_id=self.session_id,
            action_type=action_type,
            action_detail=action_detail,
            outcome=outcome,
            trust_level=self.trust_level,
            parent_record_id=None if previous is None else previous.record_id,
            prev_hash=None if previous is None else record_hash(previous),
            record_phase=record_phase,
        )
        self._sink.append(record)
        self._previous = record
        return record

    def record(
        self,
        action_type: ActionType,
        action_detail: dict[str, JSONValue],
        outcome: Outcome,
        record_phase: RecordPhase,
    ) -> AuditRecord:
        if self._closed:
            raise RuntimeError("audit session is closed")
        return self._append(action_type, action_detail, outcome, record_phase)

    def close(self) -> AuditRecord:
        if self._closed:
            raise RuntimeError("audit session is already closed")
        record = self._append(
            ActionType.LIFECYCLE,
            {"event": "session_end"},
            Outcome.SUCCESS,
            RecordPhase.POST_EXECUTION,
        )
        self._closed = True
        return record
