"""같은 과거 원문을 다시 입력해 실제 판정 완료와 카드 미생성을 검사한다."""

from __future__ import annotations

import argparse
import copy
import dataclasses
import datetime
import json
import os
from pathlib import Path
import sys

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR.parents[1]))
sys.path.insert(0, str(BASE_DIR))

import pipeline as demo
import server


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=BASE_DIR / "demo.yaml")
    parser.add_argument("--output", type=Path, default=BASE_DIR / "logs" / "negative-control-actual.json")
    args = parser.parse_args()
    os.environ.setdefault("LIVE_DEMO_GBRAIN_HOME", str(BASE_DIR / ".gbrain"))
    os.environ.pop("GBRAIN_DATABASE_URL", None)
    os.environ.pop("DATABASE_URL", None)
    config = demo.load_config(args.config)
    if config.store != "gbrain" or config.judge != "codex":
        parser.error("실제 gbrain과 Codex 설정만 검사합니다.")
    statement = copy.deepcopy(config.statements[0])
    statement["current"] = {"segment_index": 0, "text": statement["past"]["text"], "context": ""}
    config = dataclasses.replace(config, statements=(statement,))
    judge, ready = server.build_judge(config)
    if not ready:
        parser.error("Codex 로그인이 필요합니다.")
    events = []
    pipeline = demo.Pipeline(config, server.build_store(config), judge, "identical-text-control")
    pipeline.process(demo.Segment(0, 0, 1, config.holder, statement["past"]["text"]), on_event=events.append)
    verdicts = [event.payload for event in events if event.kind == "log" and "relation" in event.payload]
    completed = any(event.kind == "progress" and event.payload.get("phase") == "completed" for event in events)
    card_count = sum(event.kind == "card" for event in events)
    passed = bool(verdicts) and verdicts[-1]["relation"] == "consistent" and completed and card_count == 0
    report = {
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "scope": "같은 원문을 입력한 구성 대조군. 관계 정답·대조군 설명을 모델에 보내지 않는다. 정확도 평가 아님.",
        "provider": config.judge,
        "model": config.judge_model,
        "store": config.store,
        "completed": completed,
        "card_count": card_count,
        "verdicts": verdicts,
        "passed": passed,
        "events": [json.loads(event.to_json()) for event in events],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({key: value for key, value in report.items() if key != "events"}, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
