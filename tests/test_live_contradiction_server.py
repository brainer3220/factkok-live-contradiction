"""임시 데이터와 provider 대역으로 검증하는 실제 HTTP 계약."""

from __future__ import annotations

import contextlib
import http.client
import json
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlencode

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "demo" / "live-contradiction"))

import catalog
import pipeline as demo
import server


class Providers:
    def __init__(self):
        self.release = threading.Event()
        self.release.set()
        self.entered = threading.Condition()
        self.judged: list[str] = []
        self.failures = 0
        self.ready = True
        self.block_search_holder = None
        self.search_entered = threading.Event()
        self.search_release = threading.Event()
        self.search_release.set()

    def store(self, config):
        memory = demo.MemoryStore(config.past_takes())
        if config.holder != self.block_search_holder:
            return memory
        owner = self

        class Store:
            label = memory.label

            def search(self, holder, text, k):
                owner.search_entered.set()
                if not owner.search_release.wait(3):
                    raise demo.StoreError("테스트 검색 대기 초과")
                return memory.search(holder, text, k)

        return Store()

    def judge(self, config):
        owner = self

        class Judge:
            def judge(self, prompt):
                with owner.entered:
                    owner.judged.append(config.holder)
                    owner.entered.notify_all()
                if not owner.release.wait(5):
                    raise demo.JudgeError("테스트 응답 대기 초과")
                if owner.failures:
                    owner.failures -= 1
                    raise demo.JudgeError("테스트 판정 실패")
                return json.dumps({
                    "relation": "contradiction", "confidence": 0.9,
                    "rationale": "두 원문을 비교한 테스트 결과", "past_take_id": config.past_takes()[0].take_id,
                })

        return Judge(), self.ready

    def wait_calls(self, count):
        with self.entered:
            assert self.entered.wait_for(lambda: len(self.judged) >= count, timeout=3)


class Client:
    def __init__(self, httpd, state, providers, base_dir):
        self.httpd = httpd
        self.state = state
        self.providers = providers
        self.base_dir = base_dir

    def request(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=3)
        if isinstance(body, dict):
            body = json.dumps(body).encode("utf-8")
        connection.request(method, path, body=body, headers=headers or {})
        response = connection.getresponse()
        data = response.read()
        result = response.status, dict(response.getheaders()), data
        connection.close()
        return result

    def json(self, method, path, body=None):
        status, _, data = self.request(method, path, body)
        return status, json.loads(data)

    def session(self, person_id=None):
        status, body = self.json("POST", "/session", {} if person_id is None else {"person_id": person_id})
        assert status == 200
        return body["run_id"]

    def segment(self, run_id, index, **extra):
        return self.json("POST", "/segments", {"run_id": run_id, "index": index, **extra})[0]

    @contextlib.contextmanager
    def stream(self, run_id, *, after=None, headers=None):
        query = {"run_id": run_id}
        if after is not None:
            query["after"] = after
        connection = http.client.HTTPConnection(*self.httpd.server_address, timeout=3)
        connection.request("GET", "/events?" + urlencode(query), headers=headers or {})
        response = connection.getresponse()
        assert response.status == 200
        try:
            yield response
        finally:
            response.close()
            connection.close()

    def until(self, run_id, predicate, *, after=0):
        result = []
        with self.stream(run_id, after=after) as response:
            while True:
                event = read_event(response)
                result.append(event)
                if predicate(event):
                    return result


def read_event(response):
    fields = {}
    while True:
        line = response.readline()
        assert line, "이벤트 도착 전에 SSE가 종료됐습니다"
        line = line.decode("utf-8").rstrip("\r\n")
        if not line:
            if "data" in fields:
                event = json.loads(fields["data"])
                assert int(fields["id"]) == event["event_id"]
                return event
            continue
        if not line.startswith(":"):
            key, value = line.split(":", 1)
            fields[key] = value.strip()


def terminal(index=0, phase="completed"):
    return lambda event: event.get("segment_index") == index and event.get("phase") == phase


def write_profile(base, person_id, name, filename):
    text = f"{name}의 지금 발언"
    config = {
        "holder": name, "video": f"{filename}.mp4", "store": "memory", "judge": "codex",
        "judge_model": "test-model", "statements": [{
            "statement_id": f"{person_id}-statement", "topic": "테스트 정책",
            "past": {"text": f"{name}의 과거 발언", "date": "2025-01-01", "source_url": f"https://example.com/{person_id}"},
            "current": {
                "text": text, "date": "2026-01-01", "source_url": f"https://example.com/current/{person_id}",
                "context": "테스트 원문 맥락", "segment_index": 0, "expected_relation": "contradiction",
            },
        }],
    }
    (base / f"{filename}.yaml").write_text(yaml.safe_dump(config, allow_unicode=True), encoding="utf-8")
    (base / f"{filename}.json").write_text(json.dumps([
        {"index": index, "start": index, "end": index + 1, "speaker": name, "text": text}
        for index in range(8)
    ], ensure_ascii=False), encoding="utf-8")
    (base / f"{filename}.mp4").write_bytes(b"0123456789")
    return {
        "id": person_id, "name": name, "portrait": f"/assets/people/{person_id}.jpg",
        "config": f"{filename}.yaml", "transcript": f"{filename}.json",
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    first = write_profile(tmp_path, "person-a", "가상 인물 가", "demo")
    second = write_profile(tmp_path, "person-b", "가상 인물 나", "second")
    rows = [first, second, {"id": "no-records", "name": "미연결 인물", "portrait": "/assets/people/none.jpg"},
            {**first, "id": "wrong-holder", "name": "다른 화자"},
            {**first, "id": "outside", "config": "../outside.yaml"}]
    (tmp_path / "people.json").write_text(json.dumps({"default_person_id": "person-a", "people": rows}))
    (tmp_path / "index.html").write_text("<!doctype html><title>test</title>")
    (tmp_path / "web" / "assets" / "people").mkdir(parents=True)
    (tmp_path / "web" / "app.js").write_text("const publicAsset = true;")
    (tmp_path / "web" / "model.mjs").write_text("export const publicModule = true;")
    (tmp_path / "web" / "styles.css").write_text("body { color: black; }")
    (tmp_path / "web" / "assets" / "people" / "person-a.jpg").write_bytes(b"portrait")
    (tmp_path / ".codex-home").mkdir()
    (tmp_path / ".codex-home" / "auth.json").write_text("private fixture")
    monkeypatch.setattr(server, "BASE_DIR", tmp_path)
    providers = Providers()
    state = server.DemoState(catalog.load_catalog(tmp_path), store_factory=providers.store,
                             judge_factory=providers.judge, log_dir=tmp_path / "logs")
    httpd = server.ThreadingHTTPServer(("127.0.0.1", 0), server.make_handler(state))
    httpd.daemon_threads = True
    thread = threading.Thread(target=lambda: httpd.serve_forever(poll_interval=0.01), daemon=True)
    thread.start()
    try:
        yield Client(httpd, state, providers, tmp_path)
    finally:
        providers.release.set()
        providers.search_release.set()
        state.close()
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)


def test_people_readiness_and_config_redaction(client):
    status, people = client.json("GET", "/people")
    assert status == 200
    assert people["default_person_id"] == "person-a"
    assert [(row["id"], row["status"], row["record_count"]) for row in people["people"]] == [
        ("person-a", "ready", 1), ("person-b", "ready", 1), ("no-records", "unavailable", 0),
        ("wrong-holder", "unavailable", 0), ("outside", "unavailable", 0),
    ]
    status, config = client.json("GET", "/config?person_id=person-b")
    assert status == 200
    assert config["person_id"] == "person-b" and config["holder"] == "가상 인물 나"
    assert config["judge"] == "codex" and config["judge_model"] == "test-model"
    assert config["statements"][0]["current"]["text"] == "가상 인물 나의 지금 발언"
    serialized = json.dumps(config)
    assert "expected_relation" not in serialized and '"past"' not in serialized
    assert "second.yaml" not in serialized and str(client.base_dir) not in serialized
    status, transcript = client.json("GET", "/transcript.json?person_id=person-b")
    assert status == 200 and transcript[0]["speaker"] == "가상 인물 나"


@pytest.mark.parametrize("path", ["/config", "/transcript.json", "/video"])
def test_unavailable_and_unknown_profiles_are_explicit(client, path):
    assert client.request("GET", path + "?person_id=no-records")[0] == 409
    assert client.request("GET", path + "?person_id=wrong-holder")[0] == 409
    assert client.request("GET", path + "?person_id=missing")[0] == 404
    assert client.request("GET", path + "?person_id=")[0] == 400
    assert client.request("GET", path + "?person_id=person-a&person_id=person-b")[0] == 400
    assert client.json("POST", "/session", {"person_id": "no-records"})[0] == 409
    assert client.json("POST", "/session", {"person_id": "missing"})[0] == 404


def test_deleted_media_is_unavailable(client):
    (client.base_dir / "demo.mp4").unlink()
    assert client.json("GET", "/people")[1]["people"][0]["status"] == "unavailable"
    assert client.json("POST", "/session", {})[0] == 409


def test_session_legacy_default_and_progress_arrive_before_judge_finishes(client):
    client.providers.release.clear()
    run_id = client.session()
    assert client.segment(run_id, 0) == 202
    client.providers.wait_calls(1)
    in_flight = client.until(run_id, lambda event: event.get("stage") == "judge.start")
    assert [event["phase"] for event in in_flight if event["kind"] == "progress"] == ["searching", "judging"]
    assert not any(event["kind"] == "card" for event in in_flight)
    assert all(event["person_id"] == "person-a" for event in in_flight)
    assert client.segment(run_id, 0) == 202
    assert client.segment(run_id, 0, retry=True) == 409
    client.providers.release.set()
    events = client.until(run_id, terminal())
    cards = [event for event in events if event["kind"] == "card"]
    assert len(cards) == 1 and cards[0]["past_url"] == "https://example.com/person-a"
    assert [event["event_id"] for event in events] == list(range(1, len(events) + 1))
    assert client.providers.judged == ["가상 인물 가"]
    assert client.segment(run_id, 0) == 202
    assert client.segment(run_id, 0, retry=True) == 409
    assert len([event for event in client.until(run_id, terminal()) if event["kind"] == "card"]) == 1


def test_failed_segment_can_retry_once_and_reconnect_uses_cursor(client):
    client.providers.failures = 1
    run_id = client.session("person-b")
    assert client.segment(run_id, 0) == 202
    failed = client.until(run_id, terminal(phase="failed"))
    assert not any(event["kind"] == "card" for event in failed)
    assert client.segment(run_id, 0) == 202
    assert client.providers.judged == ["가상 인물 나"]
    assert client.segment(run_id, 0, retry=True) == 202
    success = client.until(run_id, terminal(), after=failed[-1]["event_id"])
    assert success[0]["event_id"] == failed[-1]["event_id"] + 1
    assert len([event for event in success if event["kind"] == "card"]) == 1
    first_id = success[0]["event_id"]
    with client.stream(run_id, headers={"Last-Event-ID": str(first_id)}) as response:
        assert read_event(response)["event_id"] == first_id + 1
    with client.stream(run_id, after=first_id + 1, headers={"Last-Event-ID": "0"}) as response:
        assert read_event(response)["event_id"] == first_id + 2
    with client.stream(run_id, after=0, headers={"Last-Event-ID": str(first_id)}) as response:
        assert read_event(response)["event_id"] == first_id + 1


def test_new_person_run_closes_old_stream_and_excludes_old_result(client):
    client.providers.release.clear()
    old = client.session("person-a")
    assert client.segment(old, 0) == 202
    client.providers.wait_calls(1)
    new = client.session("person-b")
    assert client.segment(old, 1) == 409
    with client.stream(old) as response:
        assert read_event(response)["kind"] == "stale"
        assert response.readline() == b""
    client.providers.release.set()
    assert client.segment(new, 0) == 202
    events = client.until(new, terminal())
    assert all(event["run_id"] == new and event["person_id"] == "person-b" for event in events)
    cards = [event for event in events if event["kind"] == "card"]
    assert len(cards) == 1
    assert cards[0]["now_text"] == "가상 인물 나의 지금 발언"
    assert cards[0]["past_url"] == "https://example.com/person-b"


def test_replaced_run_finishes_search_without_starting_a_new_judge_call(client):
    client.providers.block_search_holder = "가상 인물 가"
    client.providers.search_release.clear()
    old = client.session("person-a")
    assert client.segment(old, 0) == 202
    assert client.providers.search_entered.wait(3)
    new = client.session("person-b")
    client.providers.search_release.set()
    assert client.segment(new, 0) == 202
    events = client.until(new, terminal())
    assert any(event["kind"] == "card" for event in events)
    old_log = client.base_dir / "logs" / f"{old}.jsonl"
    deadline = time.monotonic() + 3
    while '"message": "판정 처리"' not in old_log.read_text(encoding="utf-8"):
        assert time.monotonic() < deadline, "이전 run의 검색 종료를 확인하지 못했습니다"
        threading.Event().wait(0.005)
    assert client.providers.judged == ["가상 인물 나"]
    assert '"stage": "judge.start"' not in old_log.read_text(encoding="utf-8")


def test_queue_drop_is_visible_and_skipped_segment_can_retry(client):
    client.providers.release.clear()
    run_id = client.session()
    assert client.segment(run_id, 0) == 202
    assert client.segment(run_id, 1) == 202
    client.providers.wait_calls(2)
    for index in (2, 3, 4, 5):
        assert client.segment(run_id, index) == 202
    dropped = client.until(run_id, terminal(2, "skipped"))
    assert any("대기열 초과" in event.get("message", "") for event in dropped)
    assert client.segment(run_id, 2, retry=True) == 202
    client.providers.release.set()
    retried = client.until(run_id, terminal(2), after=dropped[-1]["event_id"])
    assert any(event["kind"] == "card" and event["segment_index"] == 2 for event in retried)


def test_missing_judge_finishes_as_skipped(client):
    client.providers.ready = False
    run_id = client.session()
    assert client.segment(run_id, 0) == 202
    events = client.until(run_id, terminal(phase="skipped"))
    assert any(event["kind"] == "banner" for event in events)
    assert not any(event["kind"] == "card" for event in events)
    assert client.providers.judged == []


@pytest.mark.parametrize("value", [-1, 8, True, "0", 1.5, None, 10**100])
def test_segment_index_requires_in_range_integer(client, value):
    assert client.segment(client.session(), value) == 400


@pytest.mark.parametrize("body", [b"{", b"[]", b"null", b"\xff"])
def test_malformed_json_rejected(client, body):
    assert client.request("POST", "/session", body)[0] == 400


@pytest.mark.parametrize("length, expected", [("-1", 400), ("abc", 400), ("4097", 413)])
def test_malformed_and_large_body_lengths_rejected(client, length, expected):
    assert client.request("POST", "/session", b"{}", {"Content-Length": length})[0] == expected


@pytest.mark.parametrize("path", [
    "/events?run_id=x&after=-1", "/events?run_id=x&after=nan", "/events?run_id=x&after=1&after=2",
    "/events", "/config?person_id=../demo.yaml",
])
def test_bad_query_contracts_rejected(client, path):
    assert client.request("GET", path)[0] == 400


@pytest.mark.parametrize("path", [
    "/demo.yaml", "/people.json", "/logs/private.jsonl", "/.codex-home/auth.json", "/server.py",
    "/web/../demo.yaml", "/assets/%2e%2e/%2e%2e/.codex-home/auth.json", "/web/.hidden.css", "/web/secret.json",
])
def test_private_and_traversal_paths_are_not_served(client, path):
    assert client.request("GET", path)[0] == 404


def test_static_assets_and_symlink_escape(client, tmp_path):
    assert client.request("GET", "/")[0] == 200
    assert client.request("GET", "/web/app.js")[2] == b"const publicAsset = true;"
    assert client.request("GET", "/web/styles.css")[0] == 200
    assert client.request("GET", "/web/model.mjs")[2] == b"export const publicModule = true;"
    assert client.request("GET", "/assets/people/person-a.jpg")[2] == b"portrait"
    outside = tmp_path.parent / f"{tmp_path.name}-private.css"
    outside.write_text("private fixture")
    (tmp_path / "web" / "escape.css").symlink_to(outside)
    try:
        assert client.request("GET", "/web/escape.css")[0] == 404
    finally:
        outside.unlink()


@pytest.mark.parametrize("header, expected, content_range", [
    ("bytes=2-5", b"2345", "bytes 2-5/10"), ("bytes=7-", b"789", "bytes 7-9/10"),
    ("bytes=-3", b"789", "bytes 7-9/10"), ("bytes=8-99", b"89", "bytes 8-9/10"),
    ("bytes=-99", b"0123456789", "bytes 0-9/10"),
])
def test_video_single_ranges(client, header, expected, content_range):
    status, headers, data = client.request("GET", "/video?person_id=person-b", headers={"Range": header})
    assert status == 206 and data == expected
    assert headers["Content-Range"] == content_range
    assert headers["Content-Length"] == str(len(expected)) and headers["Accept-Ranges"] == "bytes"


@pytest.mark.parametrize("header", ["bytes=10-", "bytes=4-2", "bytes=-0", "bytes=-", "bytes=0-1,4-5", "items=0-1", "bad"])
def test_unsatisfiable_and_invalid_video_ranges(client, header):
    status, headers, data = client.request("GET", "/video", headers={"Range": header})
    assert status == 416 and data == b""
    assert headers["Content-Range"] == "bytes */10" and headers["Content-Length"] == "0"


def test_video_full_get_and_head(client):
    status, headers, data = client.request("GET", "/video")
    assert status == 200 and data == b"0123456789" and headers["Content-Length"] == "10"
    status, headers, data = client.request("HEAD", "/video", headers={"Range": "bytes=2-4"})
    assert status == 206 and data == b"" and headers["Content-Length"] == "3"
    assert headers["Content-Range"] == "bytes 2-4/10"
