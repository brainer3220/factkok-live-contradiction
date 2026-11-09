"""리허설 점수는 카드와 대조군의 완료 판정을 구분한다."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "demo" / "live-contradiction"))

import prepare  # noqa: E402


def write_inputs(tmp_path, monkeypatch, relations, events):
    statements = []
    for index, relation in enumerate(relations):
        current = {"segment_index": index, "text": f"지금 발언 {index}"}
        if relation is not None:
            current["expected_relation"] = relation
        statements.append({
            "statement_id": f"s{index}",
            "past": {
                "text": f"과거 발언 {index}",
                "date": "2024-01-01",
                "source_url": f"https://example.com/s{index}",
                "verified_at": "2026-11-09",
                "verified_by": "agent",
            },
            "current": current,
        })
    config = tmp_path / "demo.yaml"
    config.write_text(yaml.safe_dump({"holder": "화자", "statements": statements}))
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "run-1.jsonl").write_text("".join(
        json.dumps({"event": event}) + "\n" for event in events
    ))
    monkeypatch.setattr(prepare, "BASE_DIR", tmp_path)
    return config


def event(kind, index=0, statement_id="s0", relation="consistent"):
    return {
        "kind": kind,
        "segment_index": index,
        "statement_id": statement_id,
        "relation": relation,
    }


def test_legacy_card_expectation_keeps_original_counts(tmp_path, monkeypatch):
    config = write_inputs(tmp_path, monkeypatch, [None, None], [
        event("card", relation="contradiction"),
        event("card", index=7, statement_id="wrong", relation="position_change"),
    ])
    result = prepare.score(config, "run-1")
    assert result["prepared"] == 2
    assert result["correct_cards"] == 1
    assert result["missed"] == ["s1"]
    assert result["false_positives"] == [{"segment_index": 7, "statement_id": "wrong"}]
    assert result["passed"] is False


def test_positive_and_completed_control_pass(tmp_path, monkeypatch):
    config = write_inputs(tmp_path, monkeypatch, ["position_change", "consistent"], [
        event("card", relation="position_change"),
        event("log", index=1, statement_id="s1", relation="consistent"),
    ])
    result = prepare.score(config, "run-1")
    assert result["prepared"] == result["correct_cards"] == 1
    assert result["controls"] == result["correct_controls"] == 1
    assert result["passed"] is True


@pytest.mark.parametrize("events", [
    [],
    [{"kind": "log", "segment_index": 0, "message": "판정 시간 초과"}],
    [event("log", statement_id="other")],
    [event("log", relation="unrelated")],
])
def test_no_card_alone_does_not_pass_control(tmp_path, monkeypatch, events):
    config = write_inputs(tmp_path, monkeypatch, ["consistent"], events)
    result = prepare.score(config, "run-1")
    assert result["correct_controls"] == 0
    assert result["missed_controls"] == ["s0"]
    assert result["passed"] is False


def test_control_card_is_false_positive_even_with_correct_log(tmp_path, monkeypatch):
    config = write_inputs(tmp_path, monkeypatch, ["consistent"], [
        event("log"), event("card", relation="contradiction"),
    ])
    result = prepare.score(config, "run-1")
    assert result["correct_controls"] == 1
    assert result["false_positives"] == [{"segment_index": 0, "statement_id": "s0"}]
    assert result["passed"] is False


def test_explicit_relation_requires_matching_relation(tmp_path, monkeypatch):
    config = write_inputs(tmp_path, monkeypatch, ["position_change"], [
        event("card", relation="contradiction"),
    ])
    result = prepare.score(config, "run-1")
    assert result["missed"] == ["s0"]
    assert result["false_positives"]


def test_check_accepts_labelled_agent_verification_and_rejects_bad_relation(tmp_path, monkeypatch):
    config = write_inputs(tmp_path, monkeypatch, ["not_a_relation"], [])
    (tmp_path / "transcript.json").write_text(json.dumps([
        {"index": 0, "start": 0, "end": 1, "speaker": "화자", "text": "지금 발언 0"},
    ]))
    (tmp_path / "demo.mp4").write_bytes(b"\x00\x00\x00\x08moov")
    monkeypatch.setattr(prepare, "url_responds", lambda url: True)
    assert prepare.check(config) == ["s0: current.expected_relation이 유효한 판정 관계가 아닙니다."]
    data = yaml.safe_load(config.read_text())
    data["statements"][0]["current"]["expected_relation"] = "consistent"
    config.write_text(yaml.safe_dump(data))
    assert prepare.check(config) == []
    data["statements"][0]["past"]["verified_at"] = None
    config.write_text(yaml.safe_dump(data))
    assert "verified_at" in prepare.check(config)[0]


@pytest.mark.parametrize("passed, expected_exit", [(True, 0), (False, 1)])
def test_score_command_exit_status_matches_result(monkeypatch, passed, expected_exit):
    monkeypatch.setattr(sys, "argv", ["prepare.py", "score", "test-run"])
    monkeypatch.setattr(prepare, "score", lambda config_path, run_id: {"passed": passed})
    assert prepare.main() == expected_exit


def test_check_uses_adjacent_named_transcript(tmp_path, monkeypatch):
    config = write_inputs(tmp_path, monkeypatch, ["position_change"], [])
    named_config = config.rename(tmp_path / "han.yaml")
    (tmp_path / "han.transcript.json").write_text(json.dumps([
        {"index": 0, "start": 0, "end": 1, "speaker": "화자", "text": "지금 발언 0"},
    ]))
    (tmp_path / "demo.mp4").write_bytes(b"\x00\x00\x00\x08moov")
    monkeypatch.setattr(prepare, "BASE_DIR", tmp_path / "unrelated")
    monkeypatch.setattr(prepare, "url_responds", lambda url: True)
    assert prepare.check(named_config) == []


def test_explicit_transcript_takes_precedence(tmp_path, monkeypatch):
    config = write_inputs(tmp_path, monkeypatch, ["consistent"], [])
    transcript = tmp_path / "separate.json"
    transcript.write_text(json.dumps([
        {"index": 0, "start": 0, "end": 1, "speaker": "화자", "text": "지금 발언 0"},
    ]))
    (tmp_path / "transcript.json").write_text("invalid")
    (tmp_path / "demo.mp4").write_bytes(b"\x00\x00\x00\x08moov")
    monkeypatch.setattr(prepare, "url_responds", lambda url: True)
    assert prepare.check(config, transcript) == []


def test_check_verifies_current_url_when_present(tmp_path, monkeypatch):
    config = write_inputs(tmp_path, monkeypatch, ["consistent"], [])
    data = yaml.safe_load(config.read_text())
    data["statements"][0]["current"]["source_url"] = "https://example.com/current"
    config.write_text(yaml.safe_dump(data))
    (tmp_path / "transcript.json").write_text(json.dumps([
        {"index": 0, "start": 0, "end": 1, "speaker": "화자", "text": "지금 발언 0"},
    ]))
    (tmp_path / "demo.mp4").write_bytes(b"\x00\x00\x00\x08moov")
    monkeypatch.setattr(prepare, "url_responds", lambda url: not url.endswith("current"))
    assert prepare.check(config) == ["s0: current.source_url이 응답하지 않습니다."]


def test_check_cli_passes_explicit_transcript(monkeypatch):
    paths = []
    monkeypatch.setattr(sys, "argv", [
        "prepare.py", "check", "--config", "han.yaml", "--transcript", "custom.json",
    ])
    monkeypatch.setattr(prepare, "check", lambda config, transcript: paths.append((config, transcript)) or [])
    assert prepare.main() == 0
    assert paths == [(Path("han.yaml"), Path("custom.json"))]


def test_malformed_current_url_is_a_failed_check():
    assert prepare.url_responds("not a URL") is False
