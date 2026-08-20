"""Independent, signed audit-trail recorder."""

import os
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from uuid import UUID, uuid4

from pydantic import ValidationError

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
from agent_evidence.signing import RecordSigner


class RecorderError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class _ActiveSession:
    def __init__(
        self,
        agent_id: str,
        agent_version: str,
        trust_level: TrustLevel,
        sink: JsonlSink,
    ) -> None:
        self.agent_id = agent_id
        self.agent_version = agent_version
        self.trust_level = trust_level
        self.sink = sink
        self.previous: AuditRecord | None = None
        self.lock = Lock()
        self.active = True


class IndependentRecorder:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.private_key_path = data_dir / "private_key.pem"
        self.public_key_path = data_dir / "public_key.pem"
        self.trails_dir = data_dir / "trails"
        self._sessions: dict[UUID, _ActiveSession] = {}
        self._closed: set[UUID] = set()
        self._sessions_lock = Lock()
        self._initialize_storage()

    def _initialize_storage(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if os.name == "posix":
            self.data_dir.chmod(0o700)
        self.trails_dir.mkdir(exist_ok=True)
        has_trails = any(self.trails_dir.iterdir())

        if self.private_key_path.exists():
            self._signer = RecordSigner.load(self.private_key_path)
        else:
            if has_trails or self.public_key_path.exists():
                raise ValueError(
                    "private key is missing while recorder data already exists"
                )
            self._signer = RecordSigner.generate()
            self._signer.save_private_key(self.private_key_path)

        expected_public = self._signer.public_key_pem()
        if self.public_key_path.exists():
            try:
                stored_public = self.public_key_path.read_bytes()
            except OSError as exc:
                raise ValueError("public key cannot be read") from exc
            if stored_public != expected_public:
                raise ValueError("stored public key does not match the private key")
        else:
            if has_trails:
                raise ValueError("public key is missing while trails already exist")
            with self.public_key_path.open("xb") as stream:
                stream.write(expected_public)

    @property
    def component_uri(self) -> str:
        return self._signer.component_uri()

    def start_session(
        self,
        agent_id: str,
        agent_version: str,
        trust_level: TrustLevel,
    ) -> AuditRecord:
        session_id = uuid4()
        path = self.trails_dir / f"{session_id}.jsonl"
        state = _ActiveSession(agent_id, agent_version, trust_level, JsonlSink(path))
        with self._sessions_lock:
            self._sessions[session_id] = state
        try:
            return self._append(
                session_id,
                state,
                ActionType.LIFECYCLE,
                {"event": "session_start"},
                Outcome.SUCCESS,
                RecordPhase.CONCURRENT,
            )
        except Exception:
            with self._sessions_lock:
                self._sessions.pop(session_id, None)
            raise

    def _append(
        self,
        session_id: UUID,
        state: _ActiveSession,
        action_type: ActionType,
        action_detail: dict[str, JSONValue],
        outcome: Outcome,
        record_phase: RecordPhase,
        *,
        closing: bool = False,
    ) -> AuditRecord:
        with state.lock:
            if not state.active:
                raise RecorderError("SESSION_CLOSED", "Session is closed")
            previous = state.previous
            timestamp = datetime.now(UTC)
            if previous is not None and timestamp < previous.timestamp:
                timestamp = previous.timestamp
            try:
                unsigned = AuditRecord(
                    record_id=uuid4(),
                    timestamp=timestamp,
                    agent_id=state.agent_id,
                    agent_version=state.agent_version,
                    session_id=session_id,
                    action_type=action_type,
                    action_detail=action_detail,
                    outcome=outcome,
                    trust_level=state.trust_level,
                    parent_record_id=None if previous is None else previous.record_id,
                    prev_hash=None if previous is None else record_hash(previous),
                    record_phase=record_phase,
                    recording_component=self.component_uri,
                )
                signature = self._signer.sign_record(unsigned)
                record = AuditRecord.model_validate(
                    {**unsigned.json_compatible(), "signature": signature}
                )
            except ValidationError as exc:
                raise RecorderError("RECORD_INVALID", "Record is invalid") from exc
            state.sink.append(record)
            state.previous = record
            if closing:
                state.active = False
            return record

    def _active_session(self, session_id: UUID) -> _ActiveSession:
        with self._sessions_lock:
            state = self._sessions.get(session_id)
            if state is not None:
                return state
            if session_id in self._closed:
                raise RecorderError("SESSION_CLOSED", "Session is closed")
        if (self.trails_dir / f"{session_id}.jsonl").exists():
            raise RecorderError("SESSION_NOT_ACTIVE", "Session is not active")
        raise RecorderError("SESSION_NOT_FOUND", "Session does not exist")

    def record(
        self,
        session_id: UUID,
        action_type: ActionType,
        action_detail: dict[str, JSONValue],
        outcome: Outcome,
        record_phase: RecordPhase,
    ) -> AuditRecord:
        state = self._active_session(session_id)
        return self._append(
            session_id, state, action_type, action_detail, outcome, record_phase
        )

    def close_session(self, session_id: UUID) -> AuditRecord:
        state = self._active_session(session_id)
        record = self._append(
            session_id,
            state,
            ActionType.LIFECYCLE,
            {"event": "session_end"},
            Outcome.SUCCESS,
            RecordPhase.POST_EXECUTION,
            closing=True,
        )
        with self._sessions_lock:
            self._sessions.pop(session_id, None)
            self._closed.add(session_id)
        return record

    def public_key_pem(self) -> bytes:
        return self._signer.public_key_pem()
