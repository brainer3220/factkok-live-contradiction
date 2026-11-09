"""말 바꾸기 데모 파이프라인 테스트. 저장소와 판정기는 provider 경계에서만 대체한다."""

from __future__ import annotations

import dataclasses
import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "demo" / "live-contradiction"))

import pipeline as demo  # noqa: E402

HOLDER = "가상 의원 김예시"


def make_take(take_id: str, holder: str = HOLDER) -> demo.Take:
    return demo.Take(
        take_id=take_id,
        statement_id=take_id,
        holder=holder,
        text=f"{take_id} 과거 발언",
        date="2025-03-12",
        source_url=f"https://example.com/{take_id}",
        venue="가상 기자회견",
    )


class FixedStore:
    label = "test"

    def __init__(self, takes=None, error=None):
        self.takes = takes or []
        self.error = error
        self.calls = 0

    def search(self, holder, text, k):
        self.calls += 1
        if self.error:
            raise demo.StoreError(self.error)
        return [take for take in self.takes if take.holder == holder][:k]


class FixedJudge:
    def __init__(self, raw=None, error=None):
        self.raw = raw
        self.error = error
        self.calls = 0

    def judge(self, prompt):
        self.calls += 1
        if self.error:
            raise self.error
        return self.raw


def config(threshold=0.7) -> demo.DemoConfig:
    return demo.DemoConfig(
        holder=HOLDER,
        video="demo.mp4",
        store="memory",
        judge="openai",
        threshold=threshold,
        statements=(),
        base_dir=ROOT,
    )


def segment(text="국토보유세는 하지 않겠습니다.", speaker=HOLDER, index=1) -> demo.Segment:
    return demo.Segment(index=index, start=6.0, end=13.0, speaker=speaker, text=text)


def verdict(relation="contradiction", confidence=0.9, take_id="s1") -> str:
    return json.dumps(
        {"relation": relation, "confidence": confidence, "rationale": "근거", "past_take_id": take_id},
        ensure_ascii=False,
    )


def run(store, judge, seg=None, threshold=0.7):
    pipe = demo.Pipeline(config(threshold), store, judge, run_id="run-1")
    return pipe, pipe.process(seg or segment())


def kinds(events):
    return [event.kind for event in events]


def test_card_uses_segment_text_and_validated_take_source():
    take = make_take("s1")
    _, events = run(FixedStore([take, make_take("s2")]), FixedJudge(verdict()))
    cards = [event for event in events if event.kind == "card"]
    assert len(cards) == 1
    card = cards[0].payload
    assert card["now_text"] == "국토보유세는 하지 않겠습니다."
    assert card["past_url"] == take.source_url
    assert card["past_date"] == take.date
    assert card["statement_id"] == "s1"
    assert card["title"] == "다르게 말한 기록이 있음"
    assert cards[0].run_id == "run-1"


@pytest.mark.parametrize(
    "raw, error",
    [
        (verdict(take_id="s9"), None),  # 검색 결과에 없는 take
        ("not json", None),
        (json.dumps({"relation": "maybe", "confidence": 0.9, "rationale": "", "past_take_id": "s1"}), None),
        (verdict(confidence=1.5), None),
        (json.dumps({"relation": "contradiction", "confidence": True, "rationale": "", "past_take_id": "s1"}), None),
        (None, demo.JudgeError("판정 시간 초과")),
    ],
)
def test_unverifiable_verdict_never_becomes_card(raw, error):
    _, events = run(FixedStore([make_take("s1")]), FixedJudge(raw, error))
    assert "card" not in kinds(events)
    assert any("판정을 완료하지 못했습니다" in event.payload.get("message", "") for event in events)


def test_take_from_other_holder_is_rejected():
    other = make_take("s1", holder="다른 사람")
    with pytest.raises(demo.VerdictError):
        demo.parse_verdict(verdict(), [other], HOLDER)


@pytest.mark.parametrize(
    "relation, confidence",
    [("contradiction", 0.69), ("position_change", 0.5), ("consistent", 0.95), ("unrelated", 0.99)],
)
def test_non_card_verdicts_go_to_log_with_confidence(relation, confidence):
    _, events = run(FixedStore([make_take("s1")]), FixedJudge(verdict(relation, confidence)))
    assert "card" not in kinds(events)
    assert events[-1].payload["confidence"] == confidence


def test_position_change_card_title():
    _, events = run(FixedStore([make_take("s1")]), FixedJudge(verdict("position_change", 0.8)))
    assert events[-1].kind == "card"
    assert events[-1].payload["title"] == "입장이 바뀐 기록이 있음"


def test_other_speaker_is_not_searched_or_judged():
    store, judge = FixedStore([make_take("s1")]), FixedJudge(verdict())
    _, events = run(store, judge, segment(speaker="진행자"))
    assert kinds(events) == ["log"]
    assert events[0].payload["message"] == "화자 제외"
    assert store.calls == 0 and judge.calls == 0


def test_search_failure_shows_one_banner_skips_judge_and_clears_on_success():
    store, judge = FixedStore([make_take("s1")], error="timeout"), FixedJudge(verdict())
    pipe = demo.Pipeline(config(), store, judge, run_id="run-1")
    first = pipe.process(segment(index=1))
    second = pipe.process(segment(index=3))
    assert kinds(first).count("banner") == 1
    assert "banner" not in kinds(second)
    assert judge.calls == 0
    store.error = None
    third = pipe.process(segment(index=5))
    assert kinds(third)[0] == "banner_clear"
    assert "card" in kinds(third)


def test_memory_store_filters_holder_and_ranks_by_text():
    takes = [
        demo.Take("a", "a", HOLDER, "지역화폐 예산을 삭감해야 합니다", "2025-01-01", "https://e.com/a", ""),
        demo.Take("b", "b", HOLDER, "국토보유세를 도입하겠습니다", "2025-01-01", "https://e.com/b", ""),
        demo.Take("c", "c", "다른 사람", "국토보유세를 도입하겠습니다", "2025-01-01", "https://e.com/c", ""),
    ]
    found = demo.MemoryStore(takes).search(HOLDER, "국토보유세는 하지 않겠습니다", 5)
    assert [take.take_id for take in found] == ["b", "a"]


def test_sample_config_and_transcript_are_consistent():
    base = ROOT / "demo" / "live-contradiction"
    cfg = demo.load_config(base / "demo.yaml")
    segments = demo.load_transcript(base / "transcript.json")
    for statement in cfg.statements:
        seg = segments[statement["current"]["segment_index"]]
        assert seg.speaker == cfg.holder
        assert seg.text == statement["current"]["text"]
    assert all(take.holder == cfg.holder for take in cfg.past_takes())


def test_config_rejects_duplicate_statement_ids(tmp_path):
    path = tmp_path / "demo.yaml"
    past = {"text": "t", "date": "2025-01-01", "source_url": "https://e.com"}
    path.write_text(
        json.dumps(
            {"holder": "h", "statements": [{"statement_id": "s1", "past": past}, {"statement_id": "s1", "past": past}]}
        ),
        encoding="utf-8",
    )
    with pytest.raises(demo.ConfigError):
        demo.load_config(path)

def test_gbrain_results_keep_only_known_statements_with_yaml_sources():
    import gbrain_store

    known = {"s1": make_take("s1")}
    raw = json.dumps(
        [
            {"slug": "live-demo/s1", "chunk_text": "gbrain 원문"},
            {"slug": "live-demo/s1", "chunk_text": "중복"},
            {"slug": "live-demo/unknown", "chunk_text": "등록 안 됨"},
            {"slug": "people/someone", "chunk_text": "데모 밖 페이지"},
        ],
        ensure_ascii=False,
    )
    takes = gbrain_store.parse_search(raw, HOLDER, known)
    assert [take.statement_id for take in takes] == ["s1"]
    assert takes[0].text == known["s1"].text
    assert takes[0].source_url == known["s1"].source_url
    assert gbrain_store.parse_search(raw, "다른 사람", known) == []


def test_pipeline_supplies_matching_context_without_gold_relation_or_changing_quote():
    quote = "추경을 위한 조건이라면 요구를 양보하겠습니다."
    context = "2025년 예산 협상에서 조건부 양보를 밝힌 발언이다."
    statement = {
        "statement_id": "s1",
        "current": {"segment_index": 1, "context": context, "expected_relation": "position_change"},
    }
    cfg = dataclasses.replace(config(), statements=(statement,))

    class CapturingJudge:
        prompt = ""

        def judge(self, prompt):
            self.prompt = prompt
            return verdict(relation="position_change")

    judge = CapturingJudge()
    pipe = demo.Pipeline(cfg, FixedStore([make_take("s1")]), judge, run_id="context")
    cards = [event for event in pipe.process(segment(text=quote)) if event.kind == "card"]
    assert cards[0].payload["now_text"] == quote
    assert f"지금 발언: {quote}" in judge.prompt
    assert f"자료 맥락: {context}" in judge.prompt
    assert "expected_relation" not in judge.prompt
    pipe.process(segment(text=quote, index=2))
    assert context not in judge.prompt


def test_progress_callback_streams_before_provider_returns_and_preserves_result_contract():
    observed = []

    class InspectingJudge:
        def judge(self, prompt):
            assert [event.payload["phase"] for event in observed if event.kind == "progress"] == ["searching", "judging"]
            assert observed[-1].payload["stage"] == "judge.start"
            assert not any(event.kind == "card" for event in observed)
            return verdict()

    cfg = dataclasses.replace(config(), judge="codex", judge_model="test-model")
    pipe = demo.Pipeline(cfg, FixedStore([make_take("s1")]), InspectingJudge(), "progress")
    result = pipe.process(segment(), on_event=observed.append)
    assert kinds(result) == ["log", "card"]
    assert [event.payload["phase"] for event in observed if event.kind == "progress"] == ["searching", "judging", "completed"]
    progress = [event for event in observed if event.kind == "progress"]
    assert all(event.payload["provider"] == "codex" and event.payload["model"] == "test-model" for event in progress)
    assert [event.payload["stage"] for event in observed if "stage" in event.payload] == [
        "search.start", "search.end", "judge.start", "judge.end",
    ]
    assert [event for event in observed if event.kind == "card"] == [result[-1]]


@pytest.mark.parametrize("search_fails", [True, False])
def test_progress_callback_reports_terminal_failure_without_a_card(search_fails):
    observed = []
    store = FixedStore([make_take("s1")], error="unavailable" if search_fails else None)
    judge = FixedJudge(error=demo.JudgeError("unavailable"))
    pipe = demo.Pipeline(config(), store, judge, "failure")
    result = pipe.process(segment(), on_event=observed.append)
    assert observed[-1].kind == "progress" and observed[-1].payload["phase"] == "failed"
    assert not any(event.kind == "card" for event in result)
    if not search_fails:
        ended = [event for event in observed if event.payload.get("stage") == "judge.end"]
        assert len(ended) == 1 and ended[0].payload["status"] == "failed"


def test_cancelled_run_does_not_start_judging_after_search():
    active = True

    class SupersededStore:
        label = "test"

        def search(self, holder, text, k):
            nonlocal active
            active = False
            return [make_take("s1")]

    judge = FixedJudge(verdict())
    observed = []
    pipe = demo.Pipeline(config(), SupersededStore(), judge, "cancelled")
    result = pipe.process(segment(), on_event=observed.append, should_continue=lambda: active)
    assert judge.calls == 0
    assert not any(event.kind == "card" for event in result)
    assert observed[-1].payload["phase"] == "skipped"
    assert not any(event.payload.get("stage") == "judge.start" for event in observed)


def test_configured_openai_model_matches_outbound_request_and_progress(monkeypatch):
    import server

    cfg = dataclasses.replace(config(), judge="openai", judge_model="profile-model")
    monkeypatch.setenv("OPENAI_API_KEY", "synthetic-provider-fixture")
    monkeypatch.setenv("LIVE_DEMO_OPENAI_MODEL", "different-environment-model")
    requests = []

    def respond(request, *, timeout):
        requests.append(json.loads(request.data))
        return io.BytesIO(json.dumps({"choices": [{"message": {"content": verdict()}}]}).encode("utf-8"))

    monkeypatch.setattr(demo.urllib.request, "urlopen", respond)
    judge, ready = server.build_judge(cfg)
    observed = []
    result = demo.Pipeline(cfg, FixedStore([make_take("s1")]), judge, "openai-model").process(
        segment(), on_event=observed.append,
    )
    assert ready is True
    assert result[-1].kind == "card"
    assert requests[0]["model"] == cfg.judge_model
    progress = [event for event in observed if event.kind == "progress"]
    assert all(event.payload["provider"] == "openai" and event.payload["model"] == requests[0]["model"] for event in progress)
