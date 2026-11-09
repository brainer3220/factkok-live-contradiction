"""실행 중인 로컬 데모의 실제 검색·판정과 출처 연결을 검사한다.

    uv run python demo/live-contradiction/verify_demo.py --base-url http://127.0.0.1:8765

이 명령은 실제 Codex 사용량을 사용한다. 브라우저 재생 검사는 별도로 수행한다.
"""

from __future__ import annotations

import argparse
import datetime
import json
import math
from pathlib import Path
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import yaml

BASE_DIR = Path(__file__).resolve().parent


def request_json(base_url: str, path: str, body: dict | None = None) -> dict | list:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        base_url + path,
        data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.load(response)


def read_events(url: str, inbox: queue.Queue, stop: threading.Event) -> None:
    try:
        with urllib.request.urlopen(url, timeout=30) as response:
            while not stop.is_set():
                raw = response.readline()
                if not raw:
                    return
                if raw.startswith(b"data: "):
                    inbox.put(json.loads(raw[6:].decode("utf-8")))
    except (OSError, ValueError) as error:
        if not stop.is_set():
            inbox.put({"reader_error": type(error).__name__})


def expected_statements(person_id: str) -> dict:
    catalog = json.loads((BASE_DIR / "people.json").read_text(encoding="utf-8"))
    person = next(person for person in catalog["people"] if person["id"] == person_id)
    data = yaml.safe_load((BASE_DIR / person["config"]).read_text(encoding="utf-8"))
    return {statement["statement_id"]: statement for statement in data["statements"]}


def verify_video(base_url: str, person_id: str) -> dict:
    path = "/video?" + urllib.parse.urlencode({"person_id": person_id})
    request = urllib.request.Request(base_url + path, headers={"Range": "bytes=0-99"})
    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read()
        return {
            "status": response.status,
            "bytes": len(body),
            "content_range": response.headers.get("Content-Range"),
            "passed": response.status == 206 and len(body) == 100,
        }


def check_card_source(event: dict, expected: dict, by_index: dict) -> dict:
    """카드의 과거 출처와 현재 자막·영상 시점을 각각 대조한다."""
    statement_id = event.get("statement_id")
    statement = expected.get(statement_id) if isinstance(statement_id, str) else None
    matches_config_source = bool(statement) and all(
        event.get(key) == statement["past"][source_key]
        for key, source_key in (("past_text", "text"), ("past_date", "date"), ("past_url", "source_url"))
    )
    index = event.get("segment_index")
    segment = by_index.get(index) if type(index) is int and index >= 0 else None
    current_segment_found = segment is not None
    matches_current_quote = current_segment_found and (
        isinstance(segment.get("text"), str) and event.get("now_text") == segment["text"]
    )
    now_seconds = event.get("now_seconds")
    transcript_start = segment.get("start") if current_segment_found else None
    finite_times = all(
        type(value) is int or (type(value) is float and math.isfinite(value))
        for value in (now_seconds, transcript_start)
    )
    matches_current_time = finite_times and now_seconds == transcript_start
    return {
        "statement_id": statement_id,
        "segment_index": index if type(index) is int else None,
        "matches_config_source": matches_config_source,
        "current_segment_found": current_segment_found,
        "matches_current_quote": matches_current_quote,
        "matches_current_time": matches_current_time,
        "card_now_seconds": now_seconds if finite_times else None,
        "transcript_start_seconds": transcript_start if finite_times else None,
        "passed": matches_config_source and current_segment_found and matches_current_quote and matches_current_time,
    }


def verify_person(base_url: str, person: dict, timeout: float) -> dict:
    person_id = person["id"]
    query = urllib.parse.urlencode({"person_id": person_id})
    config = request_json(base_url, "/config?" + query)
    transcript = request_json(base_url, "/transcript.json?" + query)
    expected = expected_statements(person_id)
    issues = []
    indexes = {statement["current"]["segment_index"] for statement in expected.values()}
    by_index = {segment["index"]: segment for segment in transcript}
    if not config.get("judge_ready") or config.get("judge") != "codex":
        raise RuntimeError(f"{person_id}: Codex 판정기가 준비되지 않았습니다.")
    if not str(config.get("judge_model", "")).startswith("gpt-"):
        raise RuntimeError(f"{person_id}: OpenAI 모델 설정을 확인할 수 없습니다.")
    if config.get("store_label") != "gbrain":
        raise RuntimeError(f"{person_id}: 실제 gbrain 저장소가 아닙니다.")
    if any(by_index[index]["speaker"] != config["holder"] for index in indexes):
        raise RuntimeError(f"{person_id}: 자막 화자와 인물이 다릅니다.")

    video = verify_video(base_url, person_id)
    if not video["passed"]:
        issues.append("video_range")
    session = request_json(base_url, "/session", {"person_id": person_id})
    run_id = session["run_id"]
    if session.get("person_id") != person_id:
        raise RuntimeError("세션의 인물 ID가 요청과 다릅니다.")
    inbox = queue.Queue()
    stop = threading.Event()
    reader = threading.Thread(
        target=read_events,
        args=(base_url + "/events?" + urllib.parse.urlencode({"run_id": run_id}), inbox, stop),
        daemon=True,
    )
    reader.start()
    for index in sorted(indexes):
        request_json(base_url, "/segments", {"run_id": run_id, "index": index})
    print(f"{person_id} run={run_id} 실제 세그먼트 {len(indexes)}개 제출", flush=True)

    cards = {}
    completed = set()
    model_started = set()
    model_completed = set()
    searched = set()
    event_ids = []
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            try:
                event = inbox.get(timeout=min(1.0, max(0.01, deadline - time.monotonic())))
            except queue.Empty:
                if completed >= indexes:
                    break
                continue
            if event.get("reader_error"):
                issues.append("sse_" + event["reader_error"])
                break
            if event.get("run_id") != run_id or event.get("person_id") != person_id:
                issues.append("foreign_run_or_person")
                continue
            event_id = event.get("event_id")
            if isinstance(event_id, int):
                event_ids.append(event_id)
            index = event.get("segment_index")
            phase = event.get("phase")
            if event.get("kind") == "progress":
                if event.get("provider") != "codex" or event.get("model") != config["judge_model"]:
                    issues.append("provider_metadata_mismatch")
                if phase == "judging":
                    model_started.add(index)
                if phase == "completed":
                    model_completed.add(index)
                    completed.add(index)
                if phase in ("failed", "skipped"):
                    completed.add(index)
                    issues.append(f"segment_{index}_{phase}")
            if event.get("message") == "검색 완료":
                searched.add(index)
            if event.get("kind") == "card":
                statement_id = event.get("statement_id")
                if statement_id in cards:
                    issues.append("duplicate_card_" + str(statement_id))
                cards[statement_id] = event
                completed.add(index)
                print(f"{person_id} #{index} {statement_id} {event.get('relation')}", flush=True)
    finally:
        stop.set()

    source_checks = []
    for statement_id, event in cards.items():
        check = check_card_source(event, expected, by_index)
        source_checks.append(check)
        if not check["passed"]:
            issues.append("source_mismatch_" + str(statement_id))
    if set(cards) != set(expected):
        issues.append("missing_or_unexpected_cards")
    if searched != indexes:
        issues.append("missing_search_completion")
    if model_started != indexes or model_completed != indexes:
        issues.append("missing_provider_lifecycle")
    if not event_ids or event_ids != sorted(set(event_ids)):
        issues.append("invalid_event_sequence")
    return {
        "person_id": person_id,
        "run_id": run_id,
        "provider": "codex",
        "model": config["judge_model"],
        "store": config["store_label"],
        "submitted_indexes": sorted(indexes),
        "searched_indexes": sorted(searched),
        "model_started_indexes": sorted(model_started),
        "model_completed_indexes": sorted(model_completed),
        "cards": [{"statement_id": key, "relation": value.get("relation")} for key, value in cards.items()],
        "source_checks": source_checks,
        "video_range": video,
        "last_event_id": max(event_ids, default=0),
        "issues": issues,
        "passed": not issues,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765")
    parser.add_argument("--person-id", action="append", default=[])
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--output", type=Path, default=BASE_DIR / "logs" / "verification.json")
    args = parser.parse_args()
    address = urllib.parse.urlparse(args.base_url)
    if address.scheme != "http" or address.hostname not in ("127.0.0.1", "localhost", "::1"):
        parser.error("로컬 http 데모 주소만 검사합니다.")
    if args.timeout <= 0:
        parser.error("timeout은 양수여야 합니다.")
    catalog = request_json(args.base_url.rstrip("/"), "/people")
    people = [person for person in catalog["people"] if person["status"] == "ready"]
    if args.person_id:
        people = [person for person in people if person["id"] in args.person_id]
        if {person["id"] for person in people} != set(args.person_id):
            parser.error("선택한 인물의 자료가 준비되지 않았습니다.")
    if not people:
        parser.error("실제 검사할 인물이 없습니다.")
    results = []
    for person in people:
        try:
            results.append(verify_person(args.base_url.rstrip("/"), person, args.timeout))
        except (OSError, ValueError, KeyError, RuntimeError) as error:
            results.append({"person_id": person["id"], "passed": False, "issues": [str(error)]})
    report = {
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "scope": "실제 HTTP·gbrain·Codex 통합 검사. 브라우저 재생과 독립 판정 정확도 평가는 별도.",
        "people": results,
        "passed": all(result["passed"] for result in results),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
