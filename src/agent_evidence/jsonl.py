"""Append-only JSONL storage."""

import json
from pathlib import Path

from agent_evidence.models import AuditRecord

MAX_RECORD_BYTES = 256 * 1024


class JsonlSink:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, record: AuditRecord) -> None:
        line = json.dumps(
            record.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        if len(line.encode("utf-8")) > MAX_RECORD_BYTES:
            raise ValueError("serialized record exceeds 256 KiB")
        with self.path.open("a", encoding="utf-8", newline="") as stream:
            stream.write(line + "\n")
            stream.flush()
