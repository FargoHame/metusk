# Week 3 — LangGraph Adapter

Implement this entire specification sequentially in one run. Read `AGENTS.md`, `WEEK1_SPEC.md`, `WEEK2_SPEC.md`, and the existing implementation first. Preserve all passing Week 1 and Week 2 behavior. Do not pause between phases unless genuinely blocked.

Compatibility targets:

- `draft-sharif-agent-audit-trail-01`
- LangGraph 1.x
- Current `langchain-core` callback interfaces used by LangGraph 1.x

## Goal

Add one small integration that records real LangGraph tool activity through the independent Week 2 recorder.

The final demo must prove that a deterministic LangGraph run produces a signed trail containing:

1. `session_start`
2. `tool_call`
3. `tool_response`
4. `session_end`

No model API key or external LLM may be required.

## Security semantics

The adapter observes only callback events LangGraph/LangChain delivers to it. It does not prove that every side effect passed through those callbacks.

Record only hashes of tool inputs, tool outputs, and error messages. Never send raw prompts, graph state, tool parameters, tool responses, exception messages, model outputs, message histories, or secrets to the recorder.

Do not map ordinary graph-node execution or LLM callbacks to AAT `decision` records. A node run is not necessarily an agent decision, and inventing that semantic would make the trail misleading.

## Scope

Build:

- A synchronous LangGraph/LangChain callback handler
- A context-managed audit session
- Deterministic payload hashing
- Tool-call/result correlation
- Tool and graph error recording
- A deterministic LangGraph demo
- Unit and real integration tests
- Optional LangGraph package dependencies

Do not build:

- Async callback/client support
- Checkpointer wrappers
- Graph-state capture
- Prompt or completion logging
- Token streaming capture
- LLM invocation records
- Node-start or node-end records
- Decision inference
- Human-interrupt mapping
- Policy enforcement
- Automatic retries or buffering
- Offline queues
- Database storage
- OpenAI, Anthropic, or other provider integrations
- UI or reports

## Phase 1 — Inspect current APIs and packaging

Before editing, inspect the installed/current LangGraph 1.x and `langchain-core` callback signatures. Confirm the exact signatures of:

```text
BaseCallbackHandler
on_tool_start
on_tool_end
on_tool_error
on_chain_error
```

Use the actual installed type signatures. Do not copy obsolete signatures from memory.

Add LangGraph support as an optional dependency group rather than making it mandatory for the core package. Use a compatible LangGraph 1.x range and let `uv.lock` resolve the concrete tested versions.

The core package must remain importable when LangGraph is not installed. Importing `agent_evidence` must not import LangGraph eagerly.

Add only files containing real implementation:

```text
src/agent_evidence/integrations/__init__.py
src/agent_evidence/integrations/langgraph.py
tests/test_langgraph_adapter.py
```

Modify existing CLI, README, packaging, exports, and CI only where required.

## Phase 2 — Payload hashing

Inside the LangGraph integration module, implement one private helper:

```python
def _payload_hash(value: object) -> str: ...
```

Requirements:

- Return lowercase SHA-256 hexadecimal.
- Use deterministic JSON-compatible normalization.
- Canonicalize JSON-compatible values with the existing RFC 8785 path.
- Accept dictionaries, lists, strings, numbers, booleans, and null.
- Reject or safely normalize NaN and Infinity.
- For unsupported Python objects, hash only a stable type marker such as the fully qualified type name; never use `repr()` or `str()` because they may expose secret values or unstable memory addresses.
- Do not persist or log the unhashed value.
- Do not add a general-purpose serialization framework.

The hash is evidence that the adapter observed a value, not proof that the value was safe, correct, or complete.

## Phase 3 — Callback handler

Create:

```python
class LangGraphAuditCallback(BaseCallbackHandler):
    def __init__(
        self,
        client: RecorderClient,
        session_id: UUID,
    ): ...
```

The handler owns only:

- The recorder client reference
- The active recorder session ID
- A thread-safe mapping from LangChain tool `run_id` to the AAT `tool_call` record ID
- A closed flag

Set callback error behavior so recorder failures propagate instead of silently allowing an unaudited run. Confirm this behavior with the installed callback implementation and tests.

Do not store raw tool inputs, outputs, errors, graph state, prompts, or model responses in handler state.

### Tool start

Implement the installed `on_tool_start` signature.

Create an AAT record:

```text
action_type: tool_call
record_phase: pre_execution
outcome: success
```

`action_detail` contains exactly:

```json
{
  "tool_name": "<non-empty name>",
  "parameters_hash": "<sha256>"
}
```

Determine the tool name from the callback's serialized tool descriptor or explicit callback name using the smallest reliable fallback sequence. If no non-empty name exists, use `unknown_tool` rather than serializing the descriptor.

Hash the callback-provided tool input. Store only this mapping after the recorder accepts the event:

```text
LangChain run_id -> returned AAT record_id
```

Duplicate `on_tool_start` for the same active `run_id` must raise a clear adapter error rather than produce ambiguous history.

### Tool end

Implement the installed `on_tool_end` signature.

Look up the corresponding AAT tool-call record. Create:

```text
action_type: tool_response
record_phase: post_execution
outcome: success
```

`action_detail` contains exactly:

```json
{
  "tool_name": "<same tool name>",
  "response_hash": "<sha256>",
  "parent_call_id": "<AAT tool-call record UUID>"
}
```

Retain the tool name alongside the AAT record ID only if needed for correct response construction. Remove correlation state after a successful terminal event.

An end event without a matching start must raise `TOOL_START_MISSING` and must not fabricate a parent ID.

### Tool error

Implement the installed `on_tool_error` signature.

Create:

```text
action_type: error
record_phase: post_execution
outcome: failure
```

Use only:

```json
{
  "error_type": "<exception class name>",
  "message_hash": "<sha256>",
  "parent_call_id": "<AAT tool-call record UUID>"
}
```

Hash the exception message in memory; never send it. Use only the exception class name, not module paths containing application details. Remove correlation state after successful error recording.

An error without a matching tool start must raise `TOOL_START_MISSING`.

### Chain error

Do not record every `on_chain_error` callback because nested graph and node failures may emit duplicates.

Graph-level failure is recorded once by the audit-session context manager described below. The callback's `on_chain_error` must therefore either be intentionally unimplemented or a no-op, with a short comment explaining duplicate prevention.

### Unsupported callbacks

Do not override LLM, chat-model, chain-start, chain-end, retriever, text, retry, agent-action, or custom-event callbacks.

## Phase 4 — Adapter errors and state safety

Create one concise exception:

```python
class LangGraphAuditError(RuntimeError):
    code: str
```

Required codes:

```text
SESSION_CLOSED
DUPLICATE_TOOL_START
TOOL_START_MISSING
RECORDER_UNAVAILABLE
SESSION_CLOSE_FAILED
```

Preserve the underlying exception as `__cause__`, but do not include raw tool payloads or exception messages in the adapter error string.

Protect the correlation mapping with a standard thread lock because synchronous LangGraph execution may run independent tools concurrently.

Do not hold the lock during network I/O. Reserve/check state under the lock, perform the recorder call, then commit or roll back the reservation safely.

The adapter is fail-closed by default: if audit recording fails, propagate an error so the application can stop rather than continue with a false assumption of complete recording.

Do not add a fail-open option in Week 3.

## Phase 5 — Context-managed audit session

Create:

```python
class LangGraphAuditSession:
    def __init__(
        self,
        client: RecorderClient,
        agent_id: str,
        agent_version: str,
        trust_level: TrustLevel,
    ): ...

    def __enter__(self) -> "LangGraphAuditSession": ...
    def __exit__(self, exc_type, exc, traceback) -> bool: ...

    @property
    def callback(self) -> LangGraphAuditCallback: ...

    @property
    def session_id(self) -> UUID: ...
```

On entry:

1. Start one recorder session through `RecorderClient`.
2. Construct one callback bound to that session.
3. Expose the callback for LangGraph configuration.

Usage:

```python
with LangGraphAuditSession(
    client=client,
    agent_id="https://example.com/agents/calculator",
    agent_version="0.1.0",
    trust_level=TrustLevel.L2,
) as audit:
    result = graph.invoke(
        {"value": 4},
        config={"callbacks": [audit.callback]},
    )
```

On normal exit, close the recorder session.

On graph exception, first attempt to record exactly one AAT error:

```text
action_type: error
record_phase: post_execution
outcome: failure
```

Use exactly:

```json
{
  "error_type": "<exception class name>",
  "message_hash": "<sha256>"
}
```

Then close the recorder session. Do not suppress the original graph exception. If error recording or closure also fails, preserve the original exception and chain or annotate the audit failure without exposing sensitive text.

Closing lifecycle outcome remains `success` because it means the recorder successfully closed the audit session; the preceding error record represents graph failure. Document this distinction.

If entry fails, do not expose a callback or session ID. The context manager cannot be reused.

## Phase 6 — Package API

Export the integration only from `agent_evidence.integrations.langgraph` and optionally from `agent_evidence.integrations`.

Do not import it from the top-level `agent_evidence` package, because the core package must remain usable without LangGraph installed.

No changes to the recorder HTTP schema are required unless the existing implementation prevents valid `error` records. If a change is necessary, make the smallest backward-compatible change and test it.

## Phase 7 — Deterministic LangGraph demo

Add:

```bash
agent-evidence langgraph-demo \
  --url http://127.0.0.1:8765
```

The recorder must already be running. Do not start a hidden process.

Build a real, deterministic `StateGraph` with:

- A small typed state
- One node
- One local deterministic tool, such as integer multiplication
- Explicit propagation of the invocation callback configuration to the tool invocation
- No chat model or external API

Run the graph inside `LangGraphAuditSession` and print:

- Graph result
- Recorder session ID
- Relative trail location: `trails/<session-id>.jsonl`

The resulting trail must contain exactly four records:

1. lifecycle/session_start
2. tool_call
3. tool_response
4. lifecycle/session_end

If the installed LangGraph runtime emits extra callbacks, the adapter must ignore unsupported callbacks rather than create extra records.

If the optional LangGraph dependency is absent, the CLI command must fail clearly with installation guidance and exit code `2`. Other CLI commands must continue working.

## Phase 8 — Tests

Preserve every Week 1 and Week 2 test.

### Payload hashing

Test:

- Equivalent dictionaries with different key order produce the same hash.
- Nested JSON values are deterministic.
- Different values produce different hashes.
- NaN and Infinity are handled without entering the record.
- Unsupported objects use only a type marker.
- Object `repr()` and `str()` containing a secret are never called.

### Callback mapping

Test:

- Tool start creates one correct `tool_call`.
- Raw parameters do not appear in the request or record.
- Tool end creates one correct `tool_response`.
- Raw response does not appear in the request or record.
- `parent_call_id` is the AAT record ID, not the LangChain run ID.
- Tool error creates one failure record with hashed message.
- Raw exception message does not appear in the request or record.
- Successful and failed terminal events clear correlation state.
- Duplicate start raises `DUPLICATE_TOOL_START`.
- End/error without start raises `TOOL_START_MISSING`.
- Recorder failure propagates without retaining inconsistent state.
- Concurrent tool callbacks do not branch or corrupt correlation.
- Unsupported callbacks produce no records.

### Audit session

Test:

- Entry starts exactly one recorder session.
- Normal exit closes exactly once.
- Graph exception records one error, closes, and re-raises the original exception.
- Audit failure does not replace the original graph exception.
- Callback access before entry fails.
- Context manager cannot be reused.

### Real integration

Using the real FastAPI recorder and a real compiled `StateGraph`, test:

- Complete graph execution creates a valid signed trail.
- The trail has exactly four expected records.
- Correct public-key verification succeeds.
- Tool input and output plaintext are absent from the JSONL bytes.
- Modifying the trail fails verification.
- A failing tool produces a signed error record and the graph exception remains visible.

### CLI and optional dependency

Test:

- `langgraph-demo --help` works.
- Live demo succeeds with the recorder.
- Recorder unavailable exits non-zero without a traceback containing payloads.
- Core package import works when LangGraph integration is not imported.
- Missing optional dependency produces concise installation guidance.
- All existing CLI commands remain unchanged.

Do not use an LLM, provider API, or network service in tests. Use temporary directories for every recorder trail and key.

## Phase 9 — Documentation

Update README with:

- Optional LangGraph installation command
- Recorder startup command
- Minimal callback/context-manager usage
- Deterministic demo command
- Exact events captured
- Exact events intentionally not captured
- Synchronous-only limitation
- Fail-closed behavior
- Plaintext exclusion behavior
- Existing security and AAT Internet-Draft disclaimers

State clearly:

- The adapter sees only LangGraph/LangChain callback events delivered to it.
- Direct network, filesystem, subprocess, or tool activity outside callbacks is invisible.
- Hashing payloads reduces stored-data exposure but does not prove semantic correctness.
- The recorder still runs on the same host.
- This is not proof of complete agent behavior or regulatory compliance.

Do not describe graph nodes or LLM calls as decisions.

## Phase 10 — CI and final validation

Ensure CI tests both:

- Core installation without the LangGraph optional dependency
- Full test installation with the LangGraph optional dependency

Keep Python 3.11–3.13 coverage.

Run:

```bash
uv sync --all-extras
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Then perform a real integration test:

1. Start the loopback recorder.
2. Run `langgraph-demo`.
3. Verify the generated signed trail with the recorder public key.
4. Confirm exactly four records exist.
5. Search the trail bytes and confirm the raw tool input and output are absent.
6. Modify a copied record and confirm verification fails.
7. Run a graph with a failing tool and confirm an error record exists while the graph error still propagates.
8. Stop the recorder cleanly.

Fix every failure before finishing.

## Completion requirements

Week 3 is complete only when:

- Every Week 1 and Week 2 test still passes.
- The core package works without LangGraph installed.
- A real LangGraph tool call produces a signed call/response pair.
- Tool parameters, responses, and exception messages are never stored as plaintext.
- Call and response records correlate using the AAT record ID.
- Callback concurrency cannot corrupt correlation.
- Recorder failures stop the audited run rather than silently losing evidence.
- Graph exceptions remain visible to the application.
- Unsupported callbacks create no misleading records.
- The deterministic demo requires no model key.
- Signed verification and tamper detection still work.
- CI passes on Python 3.11–3.13.

## Final response

Report only:

1. What was implemented
2. Files added or changed
3. Tests and real demo results
4. Exact callback-to-AAT mapping
5. Security and privacy decisions
6. LangGraph API ambiguities encountered
7. Intentionally deferred work

## Implementation rules

- Use the installed callback signatures, not remembered APIs.
- Keep the integration optional and isolated from the core package.
- Prefer direct code over speculative abstractions.
- Never store raw callback payloads.
- Never invent AAT decisions from graph execution.
- Do not add fields or features outside this specification.
- Do not claim completion unless automated tests and the real integration test pass.
