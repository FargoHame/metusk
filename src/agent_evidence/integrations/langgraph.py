"""Synchronous LangGraph callback adapter for the independent recorder."""

import hashlib
import math
from threading import Lock
from types import TracebackType
from typing import Any, TypedDict
from uuid import UUID

import rfc8785
from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.graph import StateGraph

from agent_evidence.client import RecorderClient
from agent_evidence.models import ActionType, Outcome, RecordPhase, TrustLevel


def _type_marker(value: object) -> dict[str, str]:
    value_type = type(value)
    return {"unsupported_type": f"{value_type.__module__}.{value_type.__qualname__}"}


def _normalize_payload(value: object) -> object:
    if value is None or isinstance(value, (str, bool)):
        return value
    if isinstance(value, int):
        if -(2**53) + 1 <= value <= (2**53) - 1:
            return value
        return {"integer": format(value, "d")}
    if isinstance(value, float):
        return value if math.isfinite(value) else _type_marker(value)
    if isinstance(value, list):
        return [_normalize_payload(item) for item in value]
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        return {key: _normalize_payload(item) for key, item in value.items()}
    return _type_marker(value)


def _payload_hash(value: object) -> str:
    """Hash a safely normalized callback payload without retaining plaintext."""
    normalized = _normalize_payload(value)
    return hashlib.sha256(rfc8785.dumps(normalized)).hexdigest()


class LangGraphAuditError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class LangGraphAuditCallback(BaseCallbackHandler):
    def __init__(self, client: RecorderClient, session_id: UUID) -> None:
        self.raise_error = True
        self._client = client
        self._session_id = session_id
        self._correlations: dict[UUID, tuple[str, UUID | None, str]] = {}
        self._closed = False
        self._lock = Lock()

    def _ensure_open(self) -> None:
        if self._closed:
            raise LangGraphAuditError("SESSION_CLOSED")

    @staticmethod
    def _tool_name(serialized: dict[str, Any], kwargs: dict[str, Any]) -> str:
        for candidate in (kwargs.get("name"), serialized.get("name")):
            if isinstance(candidate, str) and candidate.strip():
                return candidate
        return "unknown_tool"

    def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        inputs: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> None:
        del parent_run_id, tags, metadata
        name = self._tool_name(serialized, kwargs)
        with self._lock:
            self._ensure_open()
            if run_id in self._correlations:
                raise LangGraphAuditError("DUPLICATE_TOOL_START")
            self._correlations[run_id] = (name, None, "starting")
        try:
            record = self._client.record(
                self._session_id,
                ActionType.TOOL_CALL,
                {
                    "tool_name": name,
                    "parameters_hash": _payload_hash(
                        inputs if inputs is not None else input_str
                    ),
                },
                Outcome.SUCCESS,
                RecordPhase.PRE_EXECUTION,
            )
        except Exception as exc:
            with self._lock:
                if self._correlations.get(run_id) == (name, None, "starting"):
                    self._correlations.pop(run_id)
            raise LangGraphAuditError("RECORDER_UNAVAILABLE") from exc
        with self._lock:
            self._ensure_open()
            self._correlations[run_id] = (name, record.record_id, "active")

    def _reserve_terminal(self, run_id: UUID) -> tuple[str, UUID]:
        with self._lock:
            self._ensure_open()
            correlation = self._correlations.get(run_id)
            if (
                correlation is None
                or correlation[1] is None
                or correlation[2] != "active"
            ):
                raise LangGraphAuditError("TOOL_START_MISSING")
            name, record_id, _state = correlation
            self._correlations[run_id] = (name, record_id, "terminal")
            return name, record_id

    def _terminal_failed(self, run_id: UUID, name: str, record_id: UUID) -> None:
        with self._lock:
            if self._correlations.get(run_id) == (name, record_id, "terminal"):
                self._correlations[run_id] = (name, record_id, "active")

    def _terminal_succeeded(self, run_id: UUID) -> None:
        with self._lock:
            self._correlations.pop(run_id, None)

    def on_tool_end(
        self,
        output: Any,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        del parent_run_id, kwargs
        name, record_id = self._reserve_terminal(run_id)
        try:
            self._client.record(
                self._session_id,
                ActionType.TOOL_RESPONSE,
                {
                    "tool_name": name,
                    "response_hash": _payload_hash(output),
                    "parent_call_id": str(record_id),
                },
                Outcome.SUCCESS,
                RecordPhase.POST_EXECUTION,
            )
        except Exception as exc:
            self._terminal_failed(run_id, name, record_id)
            raise LangGraphAuditError("RECORDER_UNAVAILABLE") from exc
        self._terminal_succeeded(run_id)

    def on_tool_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        del parent_run_id, kwargs
        name, record_id = self._reserve_terminal(run_id)
        try:
            self._client.record(
                self._session_id,
                ActionType.ERROR,
                {
                    "error_type": type(error).__name__,
                    "message_hash": _payload_hash(str(error)),
                    "parent_call_id": str(record_id),
                },
                Outcome.FAILURE,
                RecordPhase.POST_EXECUTION,
            )
        except Exception as exc:
            self._terminal_failed(run_id, name, record_id)
            raise LangGraphAuditError("RECORDER_UNAVAILABLE") from exc
        self._terminal_succeeded(run_id)

    def on_chain_error(
        self,
        error: BaseException,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        # The context manager records one graph-level error, avoiding nested duplicates.
        del error, run_id, parent_run_id, kwargs

    def _close(self) -> None:
        with self._lock:
            self._closed = True


class LangGraphAuditSession:
    def __init__(
        self,
        client: RecorderClient,
        agent_id: str,
        agent_version: str,
        trust_level: TrustLevel,
    ) -> None:
        self._client = client
        self._agent_id = agent_id
        self._agent_version = agent_version
        self._trust_level = trust_level
        self._session_id: UUID | None = None
        self._callback: LangGraphAuditCallback | None = None
        self._used = False

    def __enter__(self) -> "LangGraphAuditSession":
        if self._used:
            raise LangGraphAuditError("SESSION_CLOSED")
        self._used = True
        try:
            session_id = self._client.start_session(
                self._agent_id, self._agent_version, self._trust_level
            )
        except Exception as exc:
            raise LangGraphAuditError("RECORDER_UNAVAILABLE") from exc
        self._session_id = session_id
        self._callback = LangGraphAuditCallback(self._client, session_id)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        del exc_type, traceback
        session_id = self._session_id
        callback = self._callback
        if session_id is None or callback is None:
            return False
        audit_failure: LangGraphAuditError | None = None
        if exc is not None:
            try:
                self._client.record(
                    session_id,
                    ActionType.ERROR,
                    {
                        "error_type": type(exc).__name__,
                        "message_hash": _payload_hash(str(exc)),
                    },
                    Outcome.FAILURE,
                    RecordPhase.POST_EXECUTION,
                )
            except Exception as error:
                audit_failure = LangGraphAuditError("RECORDER_UNAVAILABLE")
                audit_failure.__cause__ = error
        try:
            self._client.close_session(session_id)
        except Exception as error:
            if audit_failure is None:
                audit_failure = LangGraphAuditError("SESSION_CLOSE_FAILED")
                audit_failure.__cause__ = error
        callback._close()
        if exc is not None:
            if audit_failure is not None:
                exc.add_note(f"Audit failure: {audit_failure.code}")
            return False
        if audit_failure is not None:
            raise audit_failure
        return False

    @property
    def callback(self) -> LangGraphAuditCallback:
        if self._callback is None:
            raise LangGraphAuditError("SESSION_CLOSED")
        return self._callback

    @property
    def session_id(self) -> UUID:
        if self._session_id is None:
            raise LangGraphAuditError("SESSION_CLOSED")
        return self._session_id


class _DemoState(TypedDict):
    value: int
    result: int


@tool
def _multiply(value: int, factor: int) -> int:
    """Multiply two integers."""
    return value * factor


def run_deterministic_demo(
    client: RecorderClient,
) -> tuple[dict[str, Any], UUID]:
    def multiply_node(state: _DemoState, config: RunnableConfig) -> dict[str, int]:
        result = _multiply.invoke(
            {"value": state["value"], "factor": 314159}, config=config
        )
        return {"result": result}

    builder = StateGraph(_DemoState)
    builder.add_node("multiply", multiply_node)
    builder.set_entry_point("multiply")
    builder.set_finish_point("multiply")
    graph = builder.compile()
    with LangGraphAuditSession(
        client,
        "https://example.com/agents/calculator",
        "0.1.0",
        TrustLevel.L2,
    ) as audit:
        result = graph.invoke(
            {"value": 987654321, "result": 0},
            config={"callbacks": [audit.callback]},
        )
        session_id = audit.session_id
    return result, session_id
