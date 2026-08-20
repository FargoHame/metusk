import json

from agent_evidence.cli import main


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
