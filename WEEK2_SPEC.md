# Week 2 — Independent Recorder and Signed Trails

Implement this entire specification sequentially in one run. Read `AGENTS.md`, `WEEK1_SPEC.md`, and the existing implementation first. Preserve all passing Week 1 behavior. Do not pause between phases unless genuinely blocked.

Compatibility target: `draft-sharif-agent-audit-trail-01`.

## Goal

Move recording outside the agent process and cryptographically sign every recorder-created record. The final demo must show that an agent sends events to a local recorder, the recorder owns chain metadata and signatures, the signed JSONL trail verifies with its public key, and modification breaks verification.

## Scope

Use FastAPI, Uvicorn, HTTPX, `cryptography`, ECDSA P-256, the existing RFC 8785 implementation, JSONL storage, and argparse CLI.

Do not build a UI, database, authentication system, cloud deployment, multi-tenancy, LangGraph integration, compliance reports, policy enforcement, redaction, remote recording, key rotation, certificate authority, queues, or multiple Uvicorn workers. Bind only to `127.0.0.1`.

## Files

Add only when they contain real implementation:

```text
src/agent_evidence/api.py
src/agent_evidence/client.py
src/agent_evidence/signing.py
src/agent_evidence/recorder.py
tests/test_api.py
tests/test_client.py
tests/test_signing.py
tests/test_recorder.py
```

Modify existing files only where required. Do not reorganize working Week 1 code without a concrete need.

## Phase 1 — Minimal schema extension

Add only two optional AAT fields to `AuditRecord`:

- `recording_component`: URI string; required on independently recorded records and omitted from old in-process trails.
- `signature`: unpadded Base64url that decodes to exactly 64 bytes containing fixed-width `r || s` (32 bytes each); omitted from old unsigned trails.

Do not add any other optional AAT fields. All Week 1 records and fixtures must continue to validate.

## Phase 2 — Signing

Create `signing.py` using ECDSA P-256 (`SECP256R1`), SHA-256, IEEE P1363 `r || s`, and unpadded Base64url.

Implement:

```python
class RecordSigner:
    @classmethod
    def generate(cls) -> "RecordSigner": ...

    @classmethod
    def load(cls, path: Path) -> "RecordSigner": ...

    def save_private_key(self, path: Path) -> None: ...
    def public_key_pem(self) -> bytes: ...
    def component_uri(self) -> str: ...
    def sign_record(self, record: AuditRecord) -> str: ...


def verify_record_signature(
    record: AuditRecord,
    public_key_pem: bytes,
) -> bool: ...
```

Signing procedure:

1. Convert the record to its JSON-compatible representation.
2. Remove the `signature` field entirely.
3. Canonicalize the remaining object with RFC 8785.
4. Calculate SHA-256 over the canonical bytes.
5. Sign that digest with ECDSA P-256 using prehashed SHA-256.
6. Convert DER ECDSA output to fixed-width 64-byte `r || s`.
7. Encode it as unpadded Base64url.

Verification must reverse that process. Return `False` for malformed or invalid signatures without exposing cryptography exceptions.

`record_hash(record)` must hash the complete record, including its signature. The order is create, sign, write, hash the complete signed record, then place that hash in the next record.

## Phase 3 — Key management

The recorder data directory contains:

```text
private_key.pem
public_key.pem
trails/
```

On first start:

- Create the data directory.
- Generate one P-256 key.
- Save the private key as PKCS8 PEM.
- Save the public key as SubjectPublicKeyInfo PEM.
- Never overwrite an existing private key.
- Use private-key mode `0600` and data-directory mode `0700` on POSIX where practical.

On later starts, load the existing key and confirm that the stored public key matches it. Fail clearly for malformed keys. Never silently replace a missing or malformed key when trails already exist.

Derive the stable recorder URI from the SHA-256 fingerprint of the DER public key:

```text
urn:agent-evidence:recorder:<lowercase-sha256>
```

Do not create a separate recorder-ID file.

## Phase 4 — Independent recorder

Create `recorder.py`:

```python
class IndependentRecorder:
    def __init__(self, data_dir: Path): ...

    def start_session(
        self,
        agent_id: str,
        agent_version: str,
        trust_level: TrustLevel,
    ) -> AuditRecord: ...

    def record(
        self,
        session_id: UUID,
        action_type: ActionType,
        action_detail: dict[str, JSONValue],
        outcome: Outcome,
        record_phase: RecordPhase,
    ) -> AuditRecord: ...

    def close_session(self, session_id: UUID) -> AuditRecord: ...
    def public_key_pem(self) -> bytes: ...
```

The recorder owns key initialization, recorder identity, active sessions, signed record creation, chaining, JSONL persistence, and closure.

Every recorder-created record must include `recording_component` and a valid signature. Clients never supply record ID, timestamp, session ID during creation, parent ID, previous hash, recorder component, or signature.

Store trails at `trails/<session_id>.jsonl`. Use one in-process lock per active session so concurrent requests cannot create branches. Do not support multiple server workers.

Existing trails must remain verifiable after restart. Resuming unfinished sessions after restart is deferred: return `SESSION_NOT_ACTIVE` rather than silently resuming or modifying an existing trail.

## Phase 5 — HTTP API

Create an application factory:

```python
def create_app(data_dir: Path) -> FastAPI: ...
```

Required endpoints:

```text
GET  /health
GET  /v1/public-key
POST /v1/sessions
POST /v1/sessions/{session_id}/records
POST /v1/sessions/{session_id}/close
```

`GET /health` returns `{"status":"ok"}`. `GET /v1/public-key` returns PEM as `text/plain`.

Start-session request:

```json
{
  "agent_id": "https://example.com/agents/refund-agent",
  "agent_version": "0.1.0",
  "trust_level": "L2"
}
```

Return status `201` with `session_id` and the signed genesis `record`.

Append request:

```json
{
  "action_type": "tool_call",
  "action_detail": {
    "tool_name": "lookup_order",
    "parameters_hash": "<sha256>"
  },
  "outcome": "success",
  "record_phase": "pre_execution"
}
```

Return status `201` with `record`. Closing also returns status `201` with the signed session-end record.

API errors use:

```json
{"error":{"code":"SESSION_NOT_FOUND","message":"Session does not exist"}}
```

Required codes:

```text
SESSION_NOT_FOUND
SESSION_NOT_ACTIVE
SESSION_CLOSED
RECORD_INVALID
RECORDER_ERROR
```

Use `404` for unknown sessions, `409` for inactive or closed sessions, `422` for invalid input, and `500` for recorder failure. Do not expose tracebacks, keys, or filesystem paths. Do not add CORS.

## Phase 6 — Python client

Create a synchronous HTTPX client:

```python
class RecorderClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8765",
        timeout: float = 10.0,
    ): ...

    def start_session(
        self,
        agent_id: str,
        agent_version: str,
        trust_level: TrustLevel,
    ) -> UUID: ...

    def record(
        self,
        session_id: UUID,
        action_type: ActionType,
        action_detail: dict[str, JSONValue],
        outcome: Outcome,
        record_phase: RecordPhase,
    ) -> AuditRecord: ...

    def close_session(self, session_id: UUID) -> AuditRecord: ...
    def get_public_key(self) -> bytes: ...
    def close(self) -> None: ...
```

Raise one concise `RecorderClientError` containing HTTP status, recorder error code, and message. Do not add retries, batching, caching, async support, or background threads.

## Phase 7 — Verification

Extend:

```python
verify_file(
    path: Path,
    public_key: Path | None = None,
) -> VerificationReport
```

Preserve all Week 1 checks. Add:

```text
RECORDING_COMPONENT_MISSING
RECORDING_COMPONENT_MISMATCH
SIGNATURE_KEY_REQUIRED
SIGNATURE_MISSING
SIGNATURE_INVALID
```

Rules:

- Completely unsigned Week 1 trails remain valid without a key.
- If any record has `recording_component`, every record must have it and use the same URI.
- If any record has a signature, every record must have one.
- Signed trails require a public key.
- Verify every signature independently.
- Verify chain hashes using complete signed records.
- Confirm that the public-key fingerprint matches `recording_component`.
- Malformed or wrong-key signatures make the trail invalid.
- Avoid cascading signature errors once the key is known to be unusable.

## Phase 8 — CLI

Extend the existing argparse CLI.

Start the recorder:

```bash
agent-evidence serve --data-dir .agent-evidence --port 8765
```

Bind only to `127.0.0.1`, use one worker, default to port `8765`, and print the data directory, recorder URI, and public-key path. Never print the private key. Do not provide `--host` or `--workers`.

Verify a signed trail:

```bash
agent-evidence verify trail.jsonl \
  --public-key .agent-evidence/public_key.pem
```

Add:

```bash
agent-evidence signed-demo --url http://127.0.0.1:8765
```

`signed-demo` starts a session through HTTP, records a tool call and tool response, closes the session, and prints the session ID and expected trail path. It must not start a hidden recorder. Preserve the Week 1 unsigned `demo` command.

## Phase 9 — Tests

Preserve all Week 1 tests and add focused coverage for:

### Signing

- Generate, save, and load a P-256 key.
- Public key and component URI remain stable.
- Valid signature passes.
- Modified record and different key fail.
- Malformed Base64url and incorrect length fail.
- Current signature is excluded from signing input.
- Previous signature is included in chain hashing.

### Recorder

- First startup generates keys; later startup loads the same key.
- Existing keys are never overwritten.
- Malformed keys fail clearly.
- Genesis and all later records are signed and contain the component URI.
- Records chain correctly.
- Concurrent appends do not branch.
- Closed, inactive, and unknown sessions fail correctly.
- Trail filename is the session UUID.

### API and client

- Health and public-key endpoints work.
- Complete start, append, and close flow works.
- Invalid input returns `422`; unknown session `404`; closed/inactive session `409`.
- Responses do not expose paths or tracebacks.
- Client parses `AuditRecord`, converts recorder errors, applies its timeout, and closes connections.

### Verification and CLI

- Existing unsigned trails still pass.
- Signed trail passes with the correct key.
- Missing or wrong keys fail.
- Edited record, missing signature, mixed component, and broken signed chain fail.
- `serve --help` works.
- Signed verification exit codes are correct.
- Existing unsigned CLI behavior remains unchanged.

Use temporary directories for every generated key and trail. Never commit test keys or generated trails.

## Phase 10 — Documentation

Update the README with:

- Independent recorder architecture.
- Recorder startup and Python client examples.
- Signed demo and verification commands.
- Private-key location and sensitivity.
- Loopback-only and restart limitations.
- Difference between hash chaining and signatures.
- Existing AAT Internet-Draft disclaimer.

State clearly that chaining detects modification of a captured trail, signing links records to the recorder key, and independent recording reduces the agent's ability to rewrite history. It still cannot prove every action was reported. Anyone holding the private key can produce apparently valid records. This does not provide compliance certification.

## Phase 11 — Final validation

Run:

```bash
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Then perform a real integration test:

1. Start the recorder.
2. Run `signed-demo`.
3. Locate the generated trail.
4. Verify it using `public_key.pem`.
5. Modify a copied record.
6. Confirm verification fails.
7. Stop the recorder cleanly.

Fix every failure before finishing.

## Completion requirements

Week 2 is complete only when all Week 1 tests still pass; recording runs outside the agent process; the server is loopback-only; keys survive restart; every recorder record is signed and contains its recorder URI; signed records are hash-chained; correct-key verification passes; missing-key, wrong-key, and mutation checks fail; concurrent requests cannot branch a session; generated keys and trails are not committed; and CI passes on Python 3.11–3.13.

## Final response

Report only:

1. What was implemented
2. Files added or changed
3. Tests and real CLI results
4. Security decisions
5. Exact AAT ambiguities
6. Intentionally deferred work

## Implementation rules

- Prefer direct code over speculative abstractions.
- Do not add fields or features outside this specification.
- Preserve Week 1 compatibility.
- Do not retain unused code.
- Do not claim completion unless automated tests and the real integration test pass.
