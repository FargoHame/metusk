# agent-evidence

`agent-evidence` is a small, deterministic, framework-neutral Python library for creating and verifying tamper-evident JSONL audit trails for AI agents. It supports unsigned in-process trails and a loopback-only independent recorder that assigns chain metadata and signs every record with ECDSA P-256. It targets **draft-sharif-agent-audit-trail-01** (<https://datatracker.ietf.org/doc/draft-sharif-agent-audit-trail/>), which is an Internet-Draft, not an official IETF standard.

## Install

Python 3.11 or newer and [uv](https://docs.astral.sh/uv/) are required:

```console
uv sync
```

LangGraph support is optional:

```console
uv sync --extra langgraph
```

## Unsigned Python API

```python
from pathlib import Path
from agent_evidence import AuditSession, ActionType, Outcome, RecordPhase, TrustLevel

session = AuditSession.start(
    "https://example.com/agents/one", "1.0.0", TrustLevel.L2, Path("trail.jsonl")
)
session.record(
    ActionType.DECISION,
    {"decision_type": "route"},
    Outcome.SUCCESS,
    RecordPhase.CONCURRENT,
)
session.close()
```

## CLI

```console
uv run agent-evidence demo --output trail.jsonl
uv run agent-evidence verify trail.jsonl
uv run agent-evidence verify trail.jsonl --json
```

## Independent recorder

The agent sends action data over loopback HTTP to a separate recorder process. The recorder owns timestamps, IDs, chain links, persistence, recorder identity, and signatures:

```console
uv run agent-evidence serve --data-dir .agent-evidence --port 8765
```

The server always binds to `127.0.0.1` with one worker. Its private key is stored at `.agent-evidence/private_key.pem`; treat this file as sensitive and do not share or commit it. The public key is `.agent-evidence/public_key.pem`, and trails are written under `.agent-evidence/trails/`.

Use the synchronous client from an agent process:

```python
from agent_evidence import ActionType, Outcome, RecordPhase, TrustLevel
from agent_evidence.client import RecorderClient

client = RecorderClient()
session_id = client.start_session(
    "https://example.com/agents/refund-agent", "0.1.0", TrustLevel.L2
)
client.record(
    session_id,
    ActionType.DECISION,
    {"decision_type": "route"},
    Outcome.SUCCESS,
    RecordPhase.CONCURRENT,
)
client.close_session(session_id)
client.close()
```

With the recorder running, create and verify a four-record signed example:

```console
uv run agent-evidence signed-demo --url http://127.0.0.1:8765
uv run agent-evidence verify .agent-evidence/trails/<session-id>.jsonl --public-key .agent-evidence/public_key.pem
```

## LangGraph adapter

The synchronous, fail-closed adapter observes tool callbacks and sends only payload hashes to the independent recorder:

```python
from agent_evidence import TrustLevel
from agent_evidence.client import RecorderClient
from agent_evidence.integrations.langgraph import LangGraphAuditSession

client = RecorderClient()
with LangGraphAuditSession(
    client,
    "https://example.com/agents/calculator",
    "0.1.0",
    TrustLevel.L2,
) as audit:
    result = graph.invoke(
        {"value": 4},
        config={"callbacks": [audit.callback]},
    )
client.close()
```

Start the recorder first, then run the deterministic local demo. It uses no model or provider API:

```console
uv run agent-evidence serve --data-dir .agent-evidence --port 8765
uv run agent-evidence langgraph-demo --url http://127.0.0.1:8765
```

The adapter records `tool_call`, `tool_response`, tool errors, and one graph-level error when execution fails. It deliberately ignores node start/end, chain start/end, LLM, chat-model, retriever, text, retry, agent-action, and custom-event callbacks; graph nodes and model calls are not labeled as decisions. The context closes the lifecycle with `success` when recorder closure succeeds, while a preceding error record represents graph failure.

Tool inputs, outputs, and exception messages are hashed in memory and are never sent or persisted as plaintext. Recorder failures propagate and stop an audited run. The adapter is synchronous only.

Each UTF-8 line is one compact JSON object. For example:

```json
{"record_id":"38c966d1-0d66-49d6-a014-8d3e622b166a","timestamp":"2026-01-01T12:00:00.000Z","agent_id":"https://example.com/agents/one","agent_version":"1.0.0","session_id":"ba86bb48-7931-43bd-9d93-06503da021bb","action_type":"lifecycle","action_detail":{"event":"session_start"},"outcome":"success","trust_level":"L2","parent_record_id":null,"prev_hash":null,"record_phase":"concurrent"}
```

Verifier error indexes are zero-based.

## Security limitations

Hash chaining detects modification of a captured trail. Signing links records to the recorder key, and recording in a separate process reduces the agent's ability to rewrite history. It still cannot prove that every action was reported truthfully, and anyone holding the private key can create apparently valid records. An in-process recorder can be compromised with the agent, while an unsigned trail can be completely regenerated by an attacker and cannot be distinguished from the original.

The recorder is loopback-only. Keys survive restarts, but unfinished sessions are deliberately not resumed after restart. Raw secrets and personal data should not be placed in `action_detail`.

The adapter sees only LangGraph/LangChain callback events delivered to it. Direct network, filesystem, subprocess, or tool activity outside callbacks is invisible. Hashing reduces stored-data exposure but does not prove semantic correctness or complete agent behavior. The recorder still runs on the same host.

This project does not provide compliance certification.
