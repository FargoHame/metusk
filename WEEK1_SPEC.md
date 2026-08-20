Build the complete Week 1 implementation in one run.

Work through the phases below sequentially. Do not stop after each phase and do not ask me to approve intermediate steps. Inspect the work after every phase, fix problems immediately, and continue until the complete implementation is tested.

# PRODUCT

Build a small framework-neutral Python library that creates and verifies tamper-evident JSONL audit trails for AI agents.

Compatibility target:

draft-sharif-agent-audit-trail-01
https://datatracker.ietf.org/doc/draft-sharif-agent-audit-trail/

This is an implementation of an Internet-Draft, not an official IETF standard.

The library must be deterministic. It must not call an LLM.

# NON-NEGOTIABLE SCOPE

Use:

- Python 3.11+
- uv
- Pydantic v2
- rfc8785 for JSON canonicalization
- SHA-256
- pytest
- Ruff
- argparse from the standard library for the CLI

Do not build:

- LangGraph integration
- OpenAI or Codex integration
- HTTP API
- database
- UI
- cloud service
- authentication
- control mapping
- compliance reports
- signatures
- policy enforcement
- redaction engine
- plugin system
- asynchronous code
- ORM models
- provider-specific models
- LLM-based verification

Do not add abstractions for hypothetical future requirements.

# DESIGN PRINCIPLE

Keep only data required by draft-sharif-agent-audit-trail-01.

Do not add optional AAT fields during this implementation.

Do not add:

- risk_score
- model_id
- human_override
- input_hash
- output_hash
- latency_ms
- cost_estimate
- jurisdiction
- signature
- nonce
- content_fingerprint
- external_timestamp
- recording_component
- schema_version
- metadata
- tags
- extensions
- created_at
- updated_at
- database IDs

If a field is not required for the record, chain, session, or verifier, do not keep it.

# PHASE 1 — INSPECT AND PLAN INTERNALLY

Inspect the existing repository before modifying it.

Preserve unrelated user changes.

Determine whether the repository already contains:

- pyproject.toml
- Python package structure
- tests
- CI
- formatting configuration

Then implement the following structure, adapting it only when the existing repository requires it:

pyproject.toml
README.md
src/agent_evidence/__init__.py
src/agent_evidence/models.py
src/agent_evidence/canonical.py
src/agent_evidence/session.py
src/agent_evidence/jsonl.py
src/agent_evidence/verify.py
src/agent_evidence/cli.py
tests/test_models.py
tests/test_canonical.py
tests/test_session.py
tests/test_verify.py
tests/test_cli.py
.github/workflows/ci.yml

Do not create empty placeholder files.

# PHASE 2 — MINIMAL RECORD MODEL

Create exactly one top-level Pydantic record model named AuditRecord.

It must contain only these AAT fields:

- record_id
- timestamp
- agent_id
- agent_version
- session_id
- action_type
- action_detail
- outcome
- trust_level
- parent_record_id
- prev_hash
- record_phase

Types and constraints:

record_id:
- UUIDv4
- serialized as a string

timestamp:
- timezone-aware datetime
- reject timestamps without a UTC offset
- serialize as RFC 3339 UTC with millisecond precision
- use a Z suffix

agent_id:
- valid URI
- serialize as a string

agent_version:
- semantic version in MAJOR.MINOR.PATCH form
- prerelease and build suffixes may be supported if validation stays concise
- do not add a semver dependency solely for this field

session_id:
- UUIDv4
- serialized as a string

action_type:
- one of:
  - tool_call
  - tool_response
  - decision
  - delegation
  - escalation
  - error
  - lifecycle

action_detail:
- dict[str, JSONValue]
- require a non-empty object
- preserve action-specific fields
- reject values that cannot be represented in JSON
- reject NaN and Infinity
- do not create separate Pydantic classes for every action

outcome:
- one of:
  - success
  - failure
  - timeout
  - denied
  - escalated

trust_level:
- one of:
  - L0
  - L1
  - L2
  - L3
  - L4

parent_record_id:
- UUIDv4 or null
- null only for the genesis record

prev_hash:
- lowercase 64-character SHA-256 hexadecimal string or null
- null only for the genesis record

record_phase:
- one of:
  - pre_execution
  - post_execution
  - concurrent

Use string enums.

Reject unknown top-level fields. We are implementing a pinned draft profile, so silently accepting misspelled top-level fields is unsafe.

# ACTION DETAIL VALIDATION

Keep action_detail as one dictionary, but validate the minimum fields required for these actions.

tool_call requires:

- tool_name: non-empty string
- parameters_hash: lowercase SHA-256 hexadecimal string

tool_response requires:

- tool_name: non-empty string
- response_hash: lowercase SHA-256 hexadecimal string
- parent_call_id: UUIDv4 string

decision requires:

- decision_type: non-empty string

lifecycle requires:

- event: session_start or session_end

For delegation, escalation, and error:

- require a non-empty action_detail object
- do not invent additional mandatory fields unless the pinned draft explicitly requires them

Put the validation close to AuditRecord. Do not build a validator registry or plugin architecture unless it makes the code shorter and clearer.

# PHASE 3 — CANONICALIZATION AND HASHING

Use the rfc8785 package. Do not write a custom RFC 8785 implementation.

Implement:

canonical_bytes(record: AuditRecord) -> bytes
record_hash(record: AuditRecord) -> str

Before canonicalization:

- convert UUIDs to strings
- convert enums to their string values
- convert URI objects to strings
- serialize timestamps in the required UTC millisecond format
- include every field in AuditRecord
- include parent_record_id and prev_hash even when null

record_hash must return:

lowercase hexadecimal SHA-256 of RFC 8785 canonical bytes

The hash of record N is placed in record N+1 as prev_hash.

# PHASE 4 — JSONL STORAGE

Implement one JsonlSink.

Its only responsibility is appending a validated AuditRecord to a file.

Requirements:

- UTF-8
- one compact JSON object per line
- append mode
- flush after each write
- final newline after each record
- do not rewrite previous records
- do not canonicalize the physical JSONL formatting
- reject a serialized record larger than 256 KiB

Do not add:

- sink protocols
- database sinks
- remote sinks
- async writing
- rotation
- compression
- cross-process locking

Those are not needed in Week 1.

# PHASE 5 — AUDIT SESSION

Implement AuditSession.

Constructor/start inputs:

- agent_id
- agent_version
- trust_level
- output path

AuditSession owns:

- session_id
- record IDs
- timestamps
- previous record
- chain linking
- JsonlSink
- open/closed state

Public API:

AuditSession.start(
    agent_id: str,
    agent_version: str,
    trust_level: TrustLevel,
    output: Path,
) -> AuditSession

session.record(
    action_type: ActionType,
    action_detail: dict[str, JSONValue],
    outcome: Outcome,
    record_phase: RecordPhase,
) -> AuditRecord

session.close() -> AuditRecord

start() must immediately create a genesis record:

- action_type = lifecycle
- action_detail = {"event": "session_start"}
- outcome = success
- record_phase = concurrent
- parent_record_id = null
- prev_hash = null

Every later record must contain:

- the same session_id
- a new UUIDv4 record_id
- parent_record_id equal to the immediately previous record ID
- prev_hash equal to record_hash(previous record)
- a timestamp not earlier than the previous timestamp

close() must append:

- action_type = lifecycle
- action_detail = {"event": "session_end"}
- outcome = success
- record_phase = post_execution

Do not add session_hash, duration_ms, record_count, enabled_tools, config_hash, recording_mode, or spec_ref. They are not required for the minimal chain.

Calling close twice must fail.

Calling record after close must fail.

Prevent callers from manually providing:

- record_id
- timestamp
- session_id
- parent_record_id
- prev_hash

# PHASE 6 — VERIFIER

Implement:

verify_file(path: Path) -> VerificationReport

VerificationReport should be a minimal dataclass or Pydantic model containing:

- valid: bool
- record_count: int
- session_id: str | None
- first_integrity_break: int | None
- errors: list[VerificationError]

VerificationError should contain:

- code
- record_index
- message

Do not include warnings, statistics, recommendations, compliance results, or duplicated record data.

Use zero-based record indexes consistently and document this.

Verify:

1. File is valid UTF-8.
2. Every non-empty line is valid JSON.
3. Every line is no larger than 256 KiB.
4. Every record passes AuditRecord validation.
5. The file contains at least two records.
6. All records have the same session_id.
7. Every record_id is unique.
8. Timestamps never move backward.
9. Record zero is a valid session_start genesis record.
10. Genesis parent_record_id is null.
11. Genesis prev_hash is null.
12. Every later parent_record_id equals the previous record_id.
13. Every later prev_hash equals record_hash(previous record).
14. Only the final record is session_end.
15. The final record is lifecycle/session_end/post_execution.
16. Non-genesis records cannot have null chain fields.

Stable error codes:

- FILE_INVALID_UTF8
- JSON_INVALID
- RECORD_TOO_LARGE
- RECORD_INVALID
- TRAIL_TOO_SHORT
- SESSION_MISMATCH
- DUPLICATE_RECORD_ID
- TIMESTAMP_REGRESSION
- GENESIS_INVALID
- PARENT_MISMATCH
- HASH_MISMATCH
- SESSION_END_INVALID
- CHAIN_FIELDS_MISSING

Do not emit cascading hash errors after the first broken link.

Continue checking independent structural rules after the first integrity break.

A deliberately rebuilt unsigned chain cannot be distinguished from the original. State this limitation clearly in the README. Signing and an independent recorder are future work.

# PHASE 7 — CLI

Use argparse. Do not add Typer or Click.

Expose the package as:

agent-evidence

Commands:

agent-evidence demo --output trail.jsonl
agent-evidence verify trail.jsonl
agent-evidence verify trail.jsonl --json

demo must create a deterministic-shaped example containing:

1. session_start
2. tool_call
3. tool_response
4. session_end

The IDs and timestamps may be generated normally.

Use SHA-256 hashes for example tool parameters and responses. Do not store raw parameters or responses.

Human verification output:

VALID
Records: 4
Session: <session-id>

Invalid output:

INVALID
First integrity break: <index>
<error-code>: <message>

JSON output must serialize the minimal VerificationReport.

Exit codes:

- 0 for a valid trail
- 1 for an invalid trail
- 2 for a CLI usage or file I/O failure

Do not add an inspect command.

# PHASE 8 — TESTS

Add focused tests for all important behavior.

Model tests:

- valid record
- missing mandatory field
- unknown top-level field
- non-v4 UUID
- timezone-naive timestamp
- invalid semantic version
- invalid enum
- uppercase or malformed hash
- empty action_detail
- invalid tool_call detail
- invalid tool_response detail
- non-JSON action_detail value
- NaN and Infinity

Canonicalization tests:

- dictionary key order does not change canonical bytes
- repeated hashing is deterministic
- nested data canonicalizes consistently
- changing one field changes the hash
- at least one relevant RFC 8785 test vector

Session tests:

- start writes genesis
- successive records link correctly
- all records share the session ID
- close writes session_end
- double close fails
- record after close fails
- JSONL contains one record per line

Verifier tests:

- valid trail
- edited record
- deleted middle record
- inserted record
- reordered records
- wrong parent ID
- wrong previous hash
- duplicate record ID
- mixed session IDs
- timestamp regression
- missing genesis
- missing session_end
- early session_end
- oversized record
- malformed JSON

CLI tests:

- demo creates a valid trail
- valid verification exits 0
- invalid verification exits 1
- missing file exits 2
- JSON output parses

Tests must verify behavior, not merely chase coverage.

# PHASE 9 — DOCUMENTATION

README must include:

- one-paragraph explanation
- installation instructions
- Python API example
- CLI demo
- example JSONL record
- exact compatibility target
- security limitations
- explicit statement that the project does not provide compliance certification
- explicit statement that the referenced document is an Internet-Draft

Keep the README practical and concise.

Document these limitations:

- An in-process recorder can be compromised with the agent.
- An unsigned trail can be completely regenerated by an attacker.
- Hash chaining detects edits to an existing captured chain.
- It does not prove that the agent reported every action truthfully.
- Raw secrets and personal data should not be placed in action_detail.
- Independent recording and signatures are future work.

# PHASE 10 — CI AND FINAL VERIFICATION

Configure GitHub Actions for Python:

- 3.11
- 3.12
- 3.13

Run:

uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest

Fix all failures.

Then run the real CLI:

1. Generate a demo trail.
2. Verify that it passes.
3. Modify a copied record.
4. Verify that the modified trail fails.

Do not claim success unless these commands actually pass.

# FINAL RESPONSE

At completion, report only:

1. What was implemented
2. Final file structure
3. Test and CLI results
4. Any exact AAT ambiguity encountered
5. Anything intentionally deferred

Do not suggest unrelated features.

# IMPLEMENTATION RULES

- Prefer straightforward functions over classes.
- AuditSession and AuditRecord are the only required domain classes.
- Do not create repository/service/manager/factory abstractions.
- Do not add optional fields “for later.”
- Do not retain unused code.
- Do not add dependencies when the standard library is sufficient.
- Do not silently reinterpret the pinned draft.
- If the draft is ambiguous, choose the smallest reasonable implementation, isolate the decision, document it, and continue.
- Complete the entire implementation in this run.