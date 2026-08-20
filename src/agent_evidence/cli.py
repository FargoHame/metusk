"""Command-line interface."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

from agent_evidence.models import ActionType, Outcome, RecordPhase, TrustLevel
from agent_evidence.session import AuditSession
from agent_evidence.verify import verify_file


def _digest(value: object) -> str:
    data = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(data).hexdigest()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-evidence")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo")
    demo.add_argument("--output", type=Path, default=Path("trail.jsonl"))
    verify = commands.add_parser("verify")
    verify.add_argument("path", type=Path)
    verify.add_argument("--json", action="store_true", dest="as_json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "demo":
            session = AuditSession.start(
                "https://example.com/agents/demo", "1.0.0", TrustLevel.L2, args.output
            )
            call = session.record(
                ActionType.TOOL_CALL,
                {"tool_name": "weather", "parameters_hash": _digest({"city": "Paris"})},
                Outcome.SUCCESS,
                RecordPhase.PRE_EXECUTION,
            )
            session.record(
                ActionType.TOOL_RESPONSE,
                {
                    "tool_name": "weather",
                    "response_hash": _digest({"temperature_c": 20}),
                    "parent_call_id": str(call.record_id),
                },
                Outcome.SUCCESS,
                RecordPhase.POST_EXECUTION,
            )
            session.close()
            return 0

        report = verify_file(args.path)
        if args.as_json:
            print(json.dumps(report.to_dict(), separators=(",", ":")))
        elif report.valid:
            print(
                f"VALID\nRecords: {report.record_count}\nSession: {report.session_id}"
            )
        else:
            print("INVALID")
            if report.first_integrity_break is not None:
                print(f"First integrity break: {report.first_integrity_break}")
            for error in report.errors:
                print(f"{error.code}: {error.message}")
        return 0 if report.valid else 1
    except OSError as exc:
        print(f"agent-evidence: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
