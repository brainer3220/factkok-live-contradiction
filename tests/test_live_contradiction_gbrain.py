"""정책명 표현 차이가 과거 발언 검색을 막지 않는지 검증한다."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "demo" / "live-contradiction"))

import gbrain_store
import pipeline


def test_search_recalls_policy_alias_without_changing_source(monkeypatch):
    take = pipeline.Take(
        take_id="stimulus", statement_id="stimulus", holder="화자",
        text="민생회복지원금을 포함해 민생회복 긴급조치를 제안드린다",
        date="2024-04-17", venue="기자회견", source_url="https://example.com/source",
    )
    query = "민생지원금 때문에 추경 못 하겠다면 포기하겠습니다."

    def provider(command, **kwargs):
        assert query in command[2]
        assert json.loads(command[command.index("--where") + 1]) == {"holder": "화자"}
        rows = []
        if "민생회복지원금" in command[2]:
            rows.append({"slug": "live-demo/stimulus", "chunk_text": take.text})
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(rows), stderr="")

    monkeypatch.setattr(gbrain_store.subprocess, "run", provider)
    found = gbrain_store.GbrainStore([take]).search("화자", query, 5)
    assert found == [take]
    assert found[0].text == "민생회복지원금을 포함해 민생회복 긴급조치를 제안드린다"


def test_reverse_alias_and_unrelated_query():
    expanded = gbrain_store.expand_policy_names("민생회복지원금 지급을 제안한다")
    assert "민생지원금" in expanded
    assert gbrain_store.expand_policy_names("기본소득 정책") == "기본소득 정책"



def test_topic_indexes_subject_without_contaminating_verified_quote():
    take = pipeline.Take(
        take_id="basic", statement_id="basic", holder="화자",
        text="모든 국민에게 연 100만원을 지급하겠다",
        date="2021-07-22", venue="기자회견", source_url="https://example.com/source",
        topic="기본소득 대선 공약",
    )
    markdown = gbrain_store.page_markdown(take)
    assert "# 기본소득 대선 공약\n\n모든 국민에게 연 100만원을 지급하겠다" in markdown
    raw = json.dumps([{"slug": "live-demo/basic", "chunk_text": "# 기본소득 대선 공약\n잘못된 검색 발췌"}])
    found = gbrain_store.parse_search(raw, "화자", {"basic": take})
    assert found == [take]
    assert found[0].text == "모든 국민에게 연 100만원을 지급하겠다"


def test_config_carries_topic_into_past_take():
    config = pipeline.load_config(ROOT / "demo/live-contradiction/demo.yaml")
    assert config.past_takes()[2].topic == config.statements[2]["topic"]
