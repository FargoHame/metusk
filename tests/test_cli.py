import json

import pytest

from agent_evidence.cli import build_parser, main
from agent_evidence.models import TrustLevel
from agent_evidence.recorder import IndependentRecorder


def test_demo_and_verify_outputs(tmp_path, capsys):
    path = tmp_path / "demo.jsonl"
    assert main(["demo", "--output", str(path)]) == 0
    assert len(path.read_text().splitlines()) == 4
    assert main(["verify", str(path)]) == 0
    assert capsys.readouterr().out.startswith("VALID\nRecords: 4")
    assert main(["verify", str(path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True


def test_invalid_and_missing_file(tmp_path, capsys):
    bad = tmp_path / "bad"
    bad.write_text("not json\n")
    assert main(["verify", str(bad)]) == 1
    assert capsys.readouterr().out.startswith("INVALID")
    assert main(["verify", str(tmp_path / "missing")]) == 2


def test_serve_help_and_signed_verification(tmp_path, capsys):
    with pytest.raises(SystemExit) as help_exit:
        build_parser().parse_args(["serve", "--help"])
    assert help_exit.value.code == 0
    recorder = IndependentRecorder(tmp_path / "data")
    genesis = recorder.start_session("https://example.com/a", "1.0.0", TrustLevel.L2)
    recorder.close_session(genesis.session_id)
    trail = tmp_path / "data" / "trails" / f"{genesis.session_id}.jsonl"
    key = tmp_path / "data" / "public_key.pem"
    assert main(["verify", str(trail), "--public-key", str(key)]) == 0
    assert capsys.readouterr().out.endswith(f"Session: {genesis.session_id}\n")
    assert main(["verify", str(trail)]) == 1
