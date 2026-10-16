"""CLI exit codes and stdout shape."""

import json
from pathlib import Path

import pytest

from skimsearch.cli import main
from skimsearch.engine import index_project

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "acme-billing"


def test_help_lists_commands():
    with pytest.raises(SystemExit) as caught:
        main(["--help"])
    assert caught.value.code == 0


def test_search_text_and_json(capsys: pytest.CaptureFixture[str], tmp_path: Path):
    (tmp_path / "fee.py").write_text(
        "def apply_late_fee():\n    return 'calculated'\n",
        encoding="utf-8",
    )
    index_project(tmp_path)
    assert main(["search", "apply_late_fee", "-C", str(tmp_path)]) == 0
    text = capsys.readouterr().out
    assert "apply_late_fee" in text
    assert "mode=lexical" in text
    assert main(["search", "apply_late_fee", "-C", str(tmp_path), "-f", "json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["cards"][0]["symbol"] == "apply_late_fee"


def test_missing_index_exits_2(capsys: pytest.CaptureFixture[str], tmp_path: Path):
    code = main(["search", "fees", "-C", str(tmp_path)])
    assert code == 2
    err = capsys.readouterr().err
    assert "skimsearch index" in err


def test_example_quickstart_command(capsys: pytest.CaptureFixture[str]):
    assert main(["index", str(EXAMPLE), "--force"]) == 0
    summary = capsys.readouterr().out
    assert "indexed" in summary
    code = main(["search", "where", "is", "the", "late", "fee", "calculated", "-C", str(EXAMPLE)])
    assert code == 0
    deck = capsys.readouterr().out
    assert "apply_late_fee" in deck
    assert "SECRET_TOKEN" not in deck
