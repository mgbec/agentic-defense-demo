import json

import pytest

import redteam.__main__ as cli
from common.constants import PROFILES
from redteam.config import ConfigError, DemoConfig

CONFIG = DemoConfig("us-east-1", "arn:aws:bedrock-agentcore:us-east-1:1:runtime/agent-x", "tbl")


class QuietAgent:
    """Answers every prompt harmlessly and does nothing."""

    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, prompt, profile):
        return {"response": "ok", "interventions": [], "trace": [], "tool_calls": []}


@pytest.fixture
def wired(sink_table, monkeypatch):
    monkeypatch.setattr(cli, "load_config", lambda: CONFIG)
    monkeypatch.setattr(cli, "point_sink_at", lambda config: None)
    monkeypatch.setattr(cli, "make_client", lambda region: None)
    monkeypatch.setattr(cli, "RuntimeInvoker", QuietAgent)


def test_parse_profiles_accepts_all_and_lists():
    assert cli.parse_profiles("all") == list(PROFILES)
    assert cli.parse_profiles("none, policy") == ["none", "policy"]


def test_parse_profiles_rejects_unknown_names():
    with pytest.raises(SystemExit, match="unknown profile"):
        cli.parse_profiles("none,maximum")


def test_list_prints_every_attack(capsys):
    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "refund-manager" in out and "legit-refund" in out and "--quick" in out


def test_a_quick_run_executes_the_quick_set_on_every_profile(wired, tmp_path, capsys):
    out_file = tmp_path / "run.json"
    code = cli.main(["run", "--quick", "--repeats", "1", "--workers", "2", "--out", str(out_file)])
    assert code == 0

    saved = json.loads(out_file.read_text())
    quick_count = sum(1 for a in saved["summary"]["attacks"] if a["quick"])
    assert saved["summary"]["runs"] == quick_count * len(PROFILES)
    assert len(saved["results"]) == saved["summary"]["runs"]
    assert "Red-team scoreboard" in capsys.readouterr().out


def test_selecting_attacks_and_profiles(wired, tmp_path):
    out_file = tmp_path / "run.json"
    cli.main(["run", "--attacks", "pii-ssn", "--profiles", "none,policy", "--repeats", "2",
              "--out", str(out_file)])
    summary = json.loads(out_file.read_text())["summary"]
    assert summary["profiles"] == ["none", "policy"]
    assert summary["runs"] == 1 * 2 * 2


def test_an_unknown_attack_id_is_a_clean_error(wired, capsys):
    assert cli.main(["run", "--attacks", "nope", "--yes"]) == 2
    assert "nope" in capsys.readouterr().err


def test_missing_deployment_is_a_clean_error(monkeypatch, capsys):
    def not_deployed():
        raise ConfigError("Nothing is deployed yet")

    monkeypatch.setattr(cli, "load_config", not_deployed)
    assert cli.main(["run", "--quick"]) == 2
    assert "Nothing is deployed" in capsys.readouterr().err


def test_run_exits_nonzero_when_any_run_failed(wired, monkeypatch, tmp_path):
    class Broken(QuietAgent):
        def __call__(self, prompt, profile):
            raise RuntimeError("runtime down")

    monkeypatch.setattr(cli, "RuntimeInvoker", Broken)
    code = cli.main(["run", "--attacks", "pii-ssn", "--profiles", "none", "--repeats", "1",
                     "--out", str(tmp_path / "r.json")])
    assert code == 1


def test_a_large_run_asks_for_confirmation(wired, monkeypatch, capsys):
    monkeypatch.setattr("builtins.input", lambda prompt: "n")
    code = cli.main(["run", "--repeats", "20"])  # all attacks x all profiles x 20
    assert code == 1
    assert "agent runs" in capsys.readouterr().out


def test_show_prints_a_saved_scoreboard(wired, tmp_path, capsys):
    out_file = tmp_path / "run.json"
    cli.main(["run", "--attacks", "pii-ssn", "--profiles", "none", "--repeats", "1",
              "--out", str(out_file)])
    capsys.readouterr()
    assert cli.main(["show", str(out_file)]) == 0
    assert "pii-ssn" in capsys.readouterr().out
