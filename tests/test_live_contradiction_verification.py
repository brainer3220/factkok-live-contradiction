"""모델 호출 없이 카드의 양쪽 원문 연결과 검증 보고서를 검사한다."""

from __future__ import annotations

import copy
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "demo" / "live-contradiction"))

import verify_demo


@pytest.fixture
def source_data():
    expected = {
        "past-tax": {
            "past": {"text": "이전 원문", "date": "2025-01-01", "source_url": "https://example.org/past"},
            "current": {"segment_index": 99},
        },
    }
    by_index = {2: {"index": 2, "start": 30.5, "end": 40.5, "speaker": "인물", "text": "현재 원문"}}
    event = {
        "kind": "card", "statement_id": "past-tax", "segment_index": 2,
        "past_text": "이전 원문", "past_date": "2025-01-01", "past_url": "https://example.org/past",
        "now_text": "현재 원문", "now_seconds": 30.5, "relation": "position_change",
    }
    return event, expected, by_index


def test_card_compares_past_id_and_current_segment_independently(source_data):
    event, expected, by_index = source_data
    original = copy.deepcopy(source_data)
    check = verify_demo.check_card_source(event, expected, by_index)
    assert check == {
        "statement_id": "past-tax", "segment_index": 2,
        "matches_config_source": True, "current_segment_found": True,
        "matches_current_quote": True, "matches_current_time": True,
        "card_now_seconds": 30.5, "transcript_start_seconds": 30.5, "passed": True,
    }
    assert source_data == original


@pytest.mark.parametrize("field", ["past_text", "past_date", "past_url"])
def test_wrong_past_source_fails_even_with_matching_current_segment(source_data, field):
    event, expected, by_index = source_data
    event[field] = "다른 출처"
    check = verify_demo.check_card_source(event, expected, by_index)
    assert check["matches_config_source"] is False
    assert check["matches_current_quote"] is True
    assert check["matches_current_time"] is True
    assert check["passed"] is False


def test_correct_past_source_does_not_hide_wrong_current_quote(source_data):
    event, expected, by_index = source_data
    event["now_text"] = "전혀 다른 현재 발언"
    check = verify_demo.check_card_source(event, expected, by_index)
    assert check["matches_config_source"] is True
    assert check["matches_current_quote"] is False
    assert check["passed"] is False


@pytest.mark.parametrize("seconds", [True, False, float("nan"), float("inf"), float("-inf"), "30.5", None, 999])
def test_invalid_or_wrong_video_time_fails(source_data, seconds):
    event, expected, by_index = source_data
    event["now_seconds"] = seconds
    check = verify_demo.check_card_source(event, expected, by_index)
    assert check["matches_config_source"] is True
    assert check["matches_current_quote"] is True
    assert check["matches_current_time"] is False
    assert check["passed"] is False


@pytest.mark.parametrize(("seconds", "start"), [(True, 1), (False, 0)])
def test_boolean_card_time_cannot_equal_numeric_transcript_start(source_data, seconds, start):
    event, expected, by_index = source_data
    event["now_seconds"] = seconds
    by_index[2]["start"] = start
    check = verify_demo.check_card_source(event, expected, by_index)
    assert check["matches_current_time"] is False
    assert check["passed"] is False


@pytest.mark.parametrize("seconds", [0, 0.0])
def test_numeric_zero_video_time_is_valid(source_data, seconds):
    event, expected, by_index = source_data
    event["now_seconds"] = seconds
    by_index[2]["start"] = 0
    check = verify_demo.check_card_source(event, expected, by_index)
    assert check["matches_current_time"] is True
    assert check["passed"] is True


@pytest.mark.parametrize("index", [999, True, "2", None, 2.0])
def test_missing_or_non_integer_current_segment_fails(source_data, index):
    event, expected, by_index = source_data
    event["segment_index"] = index
    check = verify_demo.check_card_source(event, expected, by_index)
    assert check["current_segment_found"] is False
    assert check["matches_current_quote"] is False
    assert check["matches_current_time"] is False
    assert check["passed"] is False


def test_boolean_segment_index_cannot_alias_integer_segment(source_data):
    event, expected, by_index = source_data
    by_index[1] = by_index.pop(2)
    event["segment_index"] = True
    check = verify_demo.check_card_source(event, expected, by_index)
    assert check["current_segment_found"] is False
    assert check["passed"] is False


@pytest.mark.parametrize("seconds", [True, float("nan")])
def test_invalid_transcript_start_cannot_match_card_time(source_data, seconds):
    event, expected, by_index = source_data
    event["now_seconds"] = seconds
    by_index[2]["start"] = seconds
    check = verify_demo.check_card_source(event, expected, by_index)
    assert check["matches_current_time"] is False
    assert check["passed"] is False


@pytest.mark.parametrize("wrong_current", [False, True])
def test_verification_report_rejects_the_previously_false_pass(source_data, monkeypatch, wrong_current):
    card, expected, by_index = source_data
    expected["past-tax"]["current"]["segment_index"] = 2
    if wrong_current:
        card.update(now_text="잘못 연결된 현재 발언", now_seconds=999)

    def request_json(base_url, path, body=None):
        if path.startswith("/config"):
            return {"holder": "인물", "judge_ready": True, "judge": "codex", "judge_model": "gpt-test", "store_label": "gbrain"}
        if path.startswith("/transcript"):
            return list(by_index.values())
        return {"run_id": "test-run", "person_id": "test-person"}

    def read_events(url, inbox, stop):
        events = [
            {"kind": "progress", "phase": "searching"},
            {"kind": "log", "message": "검색 완료"},
            {"kind": "progress", "phase": "judging"},
            card,
            {"kind": "progress", "phase": "completed"},
        ]
        for event_id, payload in enumerate(events, 1):
            inbox.put({
                "run_id": "test-run", "person_id": "test-person", "segment_index": 2,
                "provider": "codex", "model": "gpt-test", "event_id": event_id, **payload,
            })

    monkeypatch.setattr(verify_demo, "request_json", request_json)
    monkeypatch.setattr(verify_demo, "read_events", read_events)
    monkeypatch.setattr(verify_demo, "expected_statements", lambda person_id: expected)
    monkeypatch.setattr(verify_demo, "verify_video", lambda base_url, person_id: {"passed": True})
    report = verify_demo.verify_person("http://127.0.0.1:1", {"id": "test-person"}, 0.1)
    assert report["passed"] is not wrong_current
    assert report["issues"] == (["source_mismatch_past-tax"] if wrong_current else [])
    check = report["source_checks"][0]
    assert check["matches_config_source"] is True
    assert check["matches_current_quote"] is not wrong_current
    assert check["matches_current_time"] is not wrong_current
