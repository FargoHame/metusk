"""RFC 8785 canonicalization and record hashing."""

import hashlib

import rfc8785

from agent_evidence.models import AuditRecord


def canonical_bytes(record: AuditRecord) -> bytes:
    return rfc8785.dumps(record.json_compatible())


def record_hash(record: AuditRecord) -> str:
    return hashlib.sha256(canonical_bytes(record)).hexdigest()
