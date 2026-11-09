"""발표 전 사전검사와 리허설 채점.

    uv run python demo/live-contradiction/prepare.py check
    uv run python demo/live-contradiction/prepare.py seed
    uv run python demo/live-contradiction/prepare.py score <run_id>

check는 demo.yaml·transcript.json의 짝, 출처 URL 응답, 사람 대조 기록(verified_at),
영상 faststart 여부를 확인한다. score는 run 로그의 카드가 준비한 쌍의
statement_id를 가리키는지 센다(R9: 준비 쌍과 맞지 않는 카드는 유형과 관계없이 오탐).
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
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "Mozilla/5.0 factkok-demo-check"})
    try:
        with urllib.request.urlopen(request, timeout=URL_TIMEOUT_SECONDS) as response:
            return 200 <= response.status < 400
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def check(config_path: Path) -> list[str]:
    """문제 목록을 돌려준다. 빈 목록이면 통과."""
    problems: list[str] = []
    config = demo.load_config(config_path)
    segments = demo.load_transcript(BASE_DIR / "transcript.json")
    for statement in config.statements:
        sid = statement["statement_id"]
        current = statement.get("current") or {}
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
    video = config.base_dir / config.video
    if not video.is_file():
        problems.append(f"영상이 없습니다: {video}")
    elif not mp4_is_faststart(video):
        problems.append(f"영상이 faststart가 아닙니다. ffmpeg -i {video.name} -c copy -movflags +faststart fixed.mp4")
    return problems


def score(config_path: Path, run_id: str) -> dict[str, object]:
    config = demo.load_config(config_path)
    expected = {
        statement["current"]["segment_index"]: statement["statement_id"] for statement in config.statements
    }
    log_path = BASE_DIR / "logs" / f"{run_id}.jsonl"
    hits, false_positives = set(), []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line).get("event") or {}
        if event.get("kind") != "card":
            continue
        index, statement_id = event.get("segment_index"), event.get("statement_id")
        if expected.get(index) == statement_id:
            hits.add(statement_id)
        else:
            false_positives.append({"segment_index": index, "statement_id": statement_id})
    return {
        "prepared": len(expected),
        "correct_cards": len(hits),
        "missed": sorted(set(expected.values()) - hits),
        "false_positives": false_positives,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=("check", "seed", "score"))
    parser.add_argument("run_id", nargs="?")
    parser.add_argument("--config", type=Path, default=BASE_DIR / "demo.yaml")
    args = parser.parse_args()
    if args.command == "check":
        problems = check(args.config)
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
    print(json.dumps(score(args.config, args.run_id), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
