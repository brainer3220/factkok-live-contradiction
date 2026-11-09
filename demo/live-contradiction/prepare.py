"""발표 전 사전검사와 리허설 채점.

    uv run python demo/live-contradiction/prepare.py check
    uv run python demo/live-contradiction/prepare.py seed
    uv run python demo/live-contradiction/prepare.py score <run_id>

check는 demo.yaml·transcript.json의 짝, 출처 URL 응답, 출처 대조 기록(verified_at),
영상 faststart 여부를 확인한다. score는 run 로그의 카드가 준비한 쌍의
statement_id를 가리키는지 센다. current.expected_relation이 consistent 또는 unrelated인
대조군은 해당 관계와 statement_id가 일치하는 완료 판정 로그가 있어야 통과한다.
verified_at은 출처 대조 시점이며, agent가 대조했으면 verified_by에 agent를 명시한다.
이 기록만으로 사람의 검수나 공개 승인을 증명하지 않는다.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR.parents[1]))
sys.path.insert(0, str(BASE_DIR))

import pipeline as demo  # noqa: E402

URL_TIMEOUT_SECONDS = 8


def mp4_is_faststart(path: Path) -> bool:
    """moov 박스가 mdat보다 앞에 있으면 다 받기 전에 재생을 시작할 수 있다."""
    with path.open("rb") as handle:
        offset = 0
        size = path.stat().st_size
        while offset < size:
            handle.seek(offset)
            header = handle.read(8)
            if len(header) < 8:
                return False
            box_size = int.from_bytes(header[:4], "big")
            box_type = header[4:8]
            if box_type == b"moov":
                return True
            if box_type == b"mdat":
                return False
            if box_size == 1:
                box_size = int.from_bytes(handle.read(8), "big")
            if box_size < 8:
                return False
            offset += box_size
    return False


def url_responds(url: str) -> bool:
    if not url.startswith(("https://", "http://")):
        return False
    try:
        request = urllib.request.Request(url, method="GET", headers={"User-Agent": "Mozilla/5.0 factkok-demo-check"})
        with urllib.request.urlopen(request, timeout=URL_TIMEOUT_SECONDS) as response:
            return 200 <= response.status < 400
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return False


def transcript_for_config(config_path: Path, explicit_path: Path | None = None) -> Path:
    """명시한 자막, 설정과 이름이 같은 자막, 인접 기본 자막 순서로 고른다."""
    if explicit_path is not None:
        return explicit_path
    named = config_path.with_suffix(".transcript.json")
    return named if named.is_file() else config_path.parent / "transcript.json"


def check(config_path: Path, transcript_path: Path | None = None) -> list[str]:
    """문제 목록을 돌려준다. 빈 목록이면 통과."""
    problems: list[str] = []
    config = demo.load_config(config_path)
    segments = demo.load_transcript(transcript_for_config(config_path, transcript_path))
    for statement in config.statements:
        sid = statement["statement_id"]
        current = statement.get("current") or {}
        expected_relation = current.get("expected_relation")
        if expected_relation is not None and expected_relation not in demo.RELATIONS:
            problems.append(f"{sid}: current.expected_relation이 유효한 판정 관계가 아닙니다.")
        index = current.get("segment_index")
        if not isinstance(index, int) or not 0 <= index < len(segments):
            problems.append(f"{sid}: current.segment_index가 transcript 범위 밖입니다.")
        else:
            segment = segments[index]
            if segment.speaker != config.holder:
                problems.append(f"{sid}: 세그먼트 {index} 화자가 holder가 아닙니다({segment.speaker}).")
            if segment.text != current.get("text"):
                problems.append(f"{sid}: current.text가 세그먼트 {index} 원문과 다릅니다.")
        past = statement["past"]
        if not past.get("verified_at"):
            problems.append(f"{sid}: verified_at이 없습니다. 인용문·날짜를 원문과 대조한 뒤 기록하세요.")
        if not url_responds(str(past["source_url"])):
            problems.append(f"{sid}: source_url이 응답하지 않습니다: {past['source_url']}")
        current_url = current.get("source_url")
        if current_url and not url_responds(str(current_url)):
            problems.append(f"{sid}: current.source_url이 응답하지 않습니다.")
    video = config.base_dir / config.video
    if not video.is_file():
        problems.append(f"영상이 없습니다: {video}")
    elif not mp4_is_faststart(video):
        problems.append(f"영상이 faststart가 아닙니다. ffmpeg -i {video.name} -c copy -movflags +faststart fixed.mp4")
    return problems


def score(config_path: Path, run_id: str) -> dict[str, object]:
    config = demo.load_config(config_path)
    expected_cards = {}
    expected_controls = {}
    for statement in config.statements:
        current = statement["current"]
        relation = current.get("expected_relation")
        if relation is not None and relation not in demo.RELATIONS:
            raise demo.ConfigError("current.expected_relation이 유효한 판정 관계가 아닙니다.")
        expected = (statement["statement_id"], relation)
        if relation in ("consistent", "unrelated"):
            expected_controls[current["segment_index"]] = expected
        else:
            expected_cards[current["segment_index"]] = expected
    log_path = BASE_DIR / "logs" / f"{run_id}.jsonl"
    hits, control_hits, false_positives = set(), set(), []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line).get("event") or {}
        index, statement_id = event.get("segment_index"), event.get("statement_id")
        relation = event.get("relation")
        if event.get("kind") == "log":
            if expected_controls.get(index) == (statement_id, relation):
                control_hits.add(statement_id)
            continue
        if event.get("kind") != "card":
            continue
        expected_id, expected_relation = expected_cards.get(index, (None, None))
        if index in expected_cards and statement_id == expected_id and (
            expected_relation is None or relation == expected_relation
        ):
            hits.add(statement_id)
        else:
            false_positives.append({"segment_index": index, "statement_id": statement_id})
    missed = sorted({value[0] for value in expected_cards.values()} - hits)
    missed_controls = sorted({value[0] for value in expected_controls.values()} - control_hits)
    return {
        "prepared": len(expected_cards),
        "correct_cards": len(hits),
        "missed": missed,
        "false_positives": false_positives,
        "controls": len(expected_controls),
        "correct_controls": len(control_hits),
        "missed_controls": missed_controls,
        "passed": not missed and not missed_controls and not false_positives,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("check", "seed", "score"))
    parser.add_argument("run_id", nargs="?")
    parser.add_argument("--config", type=Path, default=BASE_DIR / "demo.yaml")
    parser.add_argument("--transcript", type=Path)
    args = parser.parse_args()
    if args.command == "check":
        problems = check(args.config, args.transcript)
        for problem in problems:
            print(f"- {problem}")
        print("통과" if not problems else f"문제 {len(problems)}건")
        return 0 if not problems else 1
    if args.command == "seed":
        from gbrain_store import GbrainStore

        config = demo.load_config(args.config)
        takes = config.past_takes()
        count = GbrainStore(takes).seed(takes)
        print(f"gbrain에 과거 발언 {count}건 저장")
        return 0
    if not args.run_id:
        parser.error("score에는 run_id가 필요합니다.")
    result = score(args.config, args.run_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
