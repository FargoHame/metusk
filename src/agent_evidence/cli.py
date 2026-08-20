"""Command-line interface."""

import argparse
import hashlib
import json
import sys
from pathlib import Path

import uvicorn

from agent_evidence.api import create_app
from agent_evidence.client import RecorderClient, RecorderClientError
from agent_evidence.models import ActionType, Outcome, RecordPhase, TrustLevel
from agent_evidence.recorder import IndependentRecorder
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
    verify.add_argument("--public-key", type=Path)
    serve = commands.add_parser("serve")
    serve.add_argument("--data-dir", type=Path, default=Path(".agent-evidence"))
    serve.add_argument("--port", type=int, default=8765)
    signed_demo = commands.add_parser("signed-demo")
    signed_demo.add_argument("--url", default="http://127.0.0.1:8765")
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

        if args.command == "serve":
            recorder = IndependentRecorder(args.data_dir)
            print(f"Data directory: {args.data_dir.resolve()}", flush=True)
            print(f"Recorder: {recorder.component_uri}", flush=True)
            print(f"Public key: {recorder.public_key_path.resolve()}", flush=True)
            uvicorn.run(
                create_app(args.data_dir), host="127.0.0.1", port=args.port, workers=1
            )
            return 0

        if args.command == "signed-demo":
            client = RecorderClient(args.url)
            try:
                session_id = client.start_session(
                    "https://example.com/agents/demo", "1.0.0", TrustLevel.L2
                )
                call = client.record(
                    session_id,
                    ActionType.TOOL_CALL,
                    {
                        "tool_name": "weather",
                        "parameters_hash": _digest({"city": "Paris"}),
                    },
                    Outcome.SUCCESS,
                    RecordPhase.PRE_EXECUTION,
                )
                client.record(
                    session_id,
                    ActionType.TOOL_RESPONSE,
                    {
                        "tool_name": "weather",
                        "response_hash": _digest({"temperature_c": 20}),
                        "parent_call_id": str(call.record_id),
                    },
                    Outcome.SUCCESS,
                    RecordPhase.POST_EXECUTION,
                )
                client.close_session(session_id)
            finally:
                client.close()
            print(f"Session: {session_id}")
            print(f"Expected trail: trails/{session_id}.jsonl")
            return 0

        report = verify_file(args.path, args.public_key)
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
    except (OSError, RecorderClientError, ValueError) as exc:
        print(f"agent-evidence: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
