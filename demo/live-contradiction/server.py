"""127.0.0.1 전용 데모 서버. 인물별 run, 작업 대기열, SSE와 정적 파일을 제공한다."""

from __future__ import annotations

import argparse
import collections
import dataclasses
import hashlib
import json
import mimetypes
import os
import re
import shutil
import sys
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR.parents[1]))
sys.path.insert(0, str(BASE_DIR))

import catalog as profiles
import pipeline as demo

BIND_HOST = "127.0.0.1"
QUEUE_LIMIT = 3
WORKERS = 2
MAX_BODY_BYTES = 4096
SSE_KEEPALIVE_SECONDS = 15
TERMINAL_PHASES = frozenset(("completed", "failed", "skipped"))


class RequestError(ValueError):
    def __init__(self, status: HTTPStatus, message: str):
        super().__init__(message)
        self.status = status


@dataclasses.dataclass(frozen=True)
class Runtime:
    profile: profiles.PersonProfile
    config: demo.DemoConfig
    store: demo.BrainStore
    judge: demo.Judge
    judge_ready: bool


class Run:
    """선택한 인물과 provider를 고정한 한 번의 재생."""

    def __init__(self, run_id: str, runtime: Runtime):
        self.run_id = run_id
        self.runtime = runtime
        self.pipeline = demo.Pipeline(runtime.config, runtime.store, runtime.judge, run_id)
        self.events: list[demo.Event] = []
        self.statuses: dict[int, str] = {}


class DemoState:
    """현재 run에 속한 작업만 실행하고 결과를 전달한다."""

    def __init__(self, catalog: profiles.Catalog, *, store_factory=None, judge_factory=None, log_dir: Path | None = None):
        self.catalog = catalog
        self.base_dir = BASE_DIR
        self.log_dir = log_dir or BASE_DIR / "logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._store_factory = store_factory or build_store
        self._judge_factory = judge_factory or build_judge
        self._runtimes: dict[str, Runtime] = {}
        self._lock = threading.Condition()
        self._log_lock = threading.Lock()
        self._queue: collections.deque[tuple[Run, int, float]] = collections.deque()
        self._closed = False
        self.current: Run | None = None
        self._workers = [threading.Thread(target=self._work, daemon=True) for _ in range(WORKERS)]
        for worker in self._workers:
            worker.start()

    def profile(self, person_id: str | None) -> profiles.PersonProfile:
        person_id = self.catalog.default_person_id if person_id is None else person_id
        if not isinstance(person_id, str) or not profiles.PERSON_ID.fullmatch(person_id):
            raise RequestError(HTTPStatus.BAD_REQUEST, "인물 ID가 잘못됐습니다")
        profile = self.catalog.people.get(person_id)
        if profile is None:
            raise RequestError(HTTPStatus.NOT_FOUND, "등록되지 않은 인물입니다")
        if not profile.ready:
            raise RequestError(HTTPStatus.CONFLICT, "이 인물의 기록·자막·영상을 준비 중입니다")
        return profile

    def runtime(self, person_id: str | None) -> Runtime:
        profile = self.profile(person_id)
        with self._lock:
            runtime = self._runtimes.get(profile.person_id)
            if runtime is None:
                config = profile.config
                if config is None:
                    raise RequestError(HTTPStatus.CONFLICT, "이 인물의 기록을 준비 중입니다")
                judge, ready = self._judge_factory(config)
                runtime = Runtime(profile, config, self._store_factory(config), judge, ready)
                self._runtimes[profile.person_id] = runtime
            return runtime

    def new_run(self, person_id: str | None = None) -> Run:
        runtime = self.runtime(person_id)
        run = Run(uuid.uuid4().hex[:12], runtime)
        with self._lock:
            self._queue.clear()
            self.current = run
            self._lock.notify_all()
        self._log(run.run_id, {
            "message": "run 시작", "person_id": runtime.profile.person_id,
            "store": runtime.store.label, "judge": runtime.config.judge,
            "model": runtime.config.judge_model,
        })
        if not runtime.judge_ready:
            self.emit(demo.Event("banner", run.run_id, {"message": "판정기 미설정", "sticky": True}))
        return run

    def enqueue(self, run_id: str, index: int, *, retry: bool = False) -> HTTPStatus:
        with self._lock:
            run = self.current
            if run is None or run.run_id != run_id:
                return HTTPStatus.CONFLICT
            if not 0 <= index < len(run.runtime.profile.segments):
                return HTTPStatus.BAD_REQUEST
            previous = run.statuses.get(index)
            if retry and previous not in ("failed", "skipped"):
                return HTTPStatus.CONFLICT
            if not retry and previous is not None:
                return HTTPStatus.ACCEPTED
            run.statuses[index] = "queued"
            self.emit(demo.Event("log", run_id, {"segment_index": index, "message": "세그먼트 수신"}))
            if not run.runtime.judge_ready:
                self.emit(demo.Event("log", run_id, {"segment_index": index, "message": "판정기 미설정으로 생략"}))
                self._progress(run, index, "skipped")
                return HTTPStatus.ACCEPTED
            if len(self._queue) >= QUEUE_LIMIT:
                dropped_run, dropped_index, _ = self._queue.popleft()
                self.emit(demo.Event("log", dropped_run.run_id, {
                    "segment_index": dropped_index, "message": "판정 생략(대기열 초과)",
                }))
                self._progress(dropped_run, dropped_index, "skipped")
            self._queue.append((run, index, time.monotonic()))
            self._lock.notify_all()
        return HTTPStatus.ACCEPTED

    def _progress(self, run: Run, index: int, phase: str) -> None:
        self.emit(demo.Event("progress", run.run_id, {
            "segment_index": index, "phase": phase,
            "provider": run.runtime.config.judge, "model": run.runtime.config.judge_model,
        }))

    def is_current(self, run: Run) -> bool:
        with self._lock:
            return self.current is run and not self._closed

    def _work(self) -> None:
        while True:
            with self._lock:
                self._lock.wait_for(lambda: bool(self._queue) or self._closed)
                if self._closed:
                    return
                run, index, enqueued_at = self._queue.popleft()
                if run is not self.current:
                    continue
                run.statuses[index] = "processing"
            started = time.monotonic()
            try:
                run.pipeline.process(
                    run.runtime.profile.segments[index], on_event=self.emit,
                    should_continue=lambda: self.is_current(run),
                )
            except Exception as error:
                self.emit(demo.Event("log", run.run_id, {
                    "segment_index": index, "message": "처리 중 예기치 않은 오류", "error_type": type(error).__name__,
                }))
                self._progress(run, index, "failed")
            self._log(run.run_id, {
                "message": "판정 처리", "segment_index": index,
                "queue_ms": int((started - enqueued_at) * 1000),
                "process_ms": int((time.monotonic() - started) * 1000),
            })

    def emit(self, event: demo.Event) -> None:
        with self._lock:
            run = self.current
            if run is not None and run.run_id == event.run_id:
                event = dataclasses.replace(event, payload={
                    **event.payload, "person_id": run.runtime.profile.person_id,
                    "event_id": len(run.events) + 1,
                })
                index, phase = event.payload.get("segment_index"), event.payload.get("phase")
                if event.kind == "progress" and phase in TERMINAL_PHASES:
                    run.statuses[index] = phase
                run.events.append(event)
                self._lock.notify_all()
        self._log(event.run_id, {"event": json.loads(event.to_json())})

    def wait_events(self, run_id: str, after: int, timeout: float) -> list[demo.Event] | None:
        with self._lock:
            deadline = time.monotonic() + timeout
            while True:
                run = self.current
                if run is None or run.run_id != run_id:
                    return None
                if len(run.events) > after:
                    return run.events[after:]
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return []
                self._lock.wait(remaining)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._queue.clear()
            self.current = None
            self._lock.notify_all()
        for worker in self._workers:
            worker.join(timeout=1)

    def _log(self, run_id: str, record: dict) -> None:
        record = {"ts": round(time.time(), 3), "run_id": run_id, **record}
        with self._log_lock:
            with (self.log_dir / f"{run_id}.jsonl").open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def make_handler(state: DemoState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "LiveContradictionDemo/2"

        def log_message(self, format, *args):
            return

        def _json(self, status: HTTPStatus, body: object) -> None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(data)

        def _file(self, path: Path, content_type: str, *, video: bool = False) -> None:
            try:
                handle = path.open("rb")
            except OSError:
                self._json(HTTPStatus.NOT_FOUND, {"error": "파일이 없습니다"})
                return
            with handle:
                size = os.fstat(handle.fileno()).st_size
                start, end, status = 0, size - 1, HTTPStatus.OK
                ranges = self.headers.get_all("Range", [])
                if video and ranges:
                    match = re.fullmatch(r"bytes=(\d*)-(\d*)", ranges[0].strip()) if len(ranges) == 1 and len(ranges[0]) < 200 else None
                    if match is not None and size:
                        left, right = match.groups()
                        if left:
                            start = int(left)
                            end = min(int(right), size - 1) if right else size - 1
                        elif right and int(right) > 0:
                            start, end = max(0, size - int(right)), size - 1
                        else:
                            match = None
                    if match is None or not size or start >= size or end < start:
                        self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                        self.send_header("Content-Range", f"bytes */{size}")
                        self.send_header("Accept-Ranges", "bytes")
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    status = HTTPStatus.PARTIAL_CONTENT
                self.send_response(status)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(max(0, end - start + 1)))
                self.send_header("Cache-Control", "no-store")
                if video:
                    self.send_header("Accept-Ranges", "bytes")
                if status == HTTPStatus.PARTIAL_CONTENT:
                    self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
                self.end_headers()
                if self.command == "HEAD":
                    return
                handle.seek(start)
                remaining = end - start + 1
                try:
                    while remaining > 0:
                        data = handle.read(min(64 * 1024, remaining))
                        if not data:
                            break
                        self.wfile.write(data)
                        remaining -= len(data)
                except (BrokenPipeError, ConnectionResetError):
                    return

        def _person_id(self, query: dict[str, list[str]]) -> str | None:
            values = query.get("person_id")
            if values is None:
                return None
            if len(values) != 1 or not values[0]:
                raise RequestError(HTTPStatus.BAD_REQUEST, "인물 ID가 잘못됐습니다")
            return values[0]

        def _static(self, path: str) -> None:
            if any(part.startswith(".") for part in Path(path).parts):
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            if path in ("/", "/index.html"):
                target, root = state.base_dir / "index.html", state.base_dir
            elif path.startswith("/assets/"):
                root = state.base_dir / "web" / "assets"
                target = root / path.removeprefix("/assets/")
            elif path.startswith("/web/") and Path(path).suffix in (".js", ".mjs", ".css"):
                root = state.base_dir / "web"
                target = root / path.removeprefix("/web/")
            elif path in ("/app.js", "/app.css"):
                root = state.base_dir / "web"
                target = root / path.lstrip("/")
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            if not target.resolve().is_relative_to(root.resolve()):
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            if content_type.startswith(("text/", "application/javascript", "application/json")):
                content_type += "; charset=utf-8"
            self._file(target, content_type)

        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            try:
                url = urlparse(self.path)
                path = unquote(url.path, errors="strict")
                query = parse_qs(url.query, keep_blank_values=True)
                if path == "/people":
                    self._json(HTTPStatus.OK, state.catalog.public_metadata())
                elif path in ("/config", "/transcript.json", "/video"):
                    person_id = self._person_id(query)
                    profile = state.profile(person_id)
                    if path == "/config":
                        runtime = state.runtime(person_id)
                        statements = [{
                            "statement_id": row["statement_id"], "topic": row.get("topic", ""),
                            "current": {key: row["current"].get(key) for key in ("text", "date", "source_url", "context", "segment_index")},
                        } for row in runtime.config.statements]
                        self._json(HTTPStatus.OK, {
                            "person_id": profile.person_id, "holder": runtime.config.holder,
                            "store_label": runtime.store.label, "judge_ready": runtime.judge_ready,
                            "judge": runtime.config.judge, "judge_model": runtime.config.judge_model,
                            "statements": statements,
                        })
                    elif path == "/transcript.json":
                        self._json(HTTPStatus.OK, [dataclasses.asdict(segment) for segment in profile.segments])
                    else:
                        self._file(profile.video_path, "video/mp4", video=True)
                elif path == "/events" and self.command != "HEAD":
                    run_ids = query.get("run_id", [])
                    if len(run_ids) != 1 or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", run_ids[0]):
                        raise RequestError(HTTPStatus.BAD_REQUEST, "run_id가 잘못됐습니다")
                    cursors = query.get("after", ["0"])
                    last_ids = self.headers.get_all("Last-Event-ID", [])
                    if len(cursors) != 1 or len(last_ids) > 1:
                        raise RequestError(HTTPStatus.BAD_REQUEST, "이벤트 위치가 잘못됐습니다")
                    cursors += last_ids
                    if any(not re.fullmatch(r"[0-9]{1,18}", cursor) for cursor in cursors):
                        raise RequestError(HTTPStatus.BAD_REQUEST, "이벤트 위치가 잘못됐습니다")
                    self._events(run_ids[0], max(int(cursor) for cursor in cursors))
                else:
                    self._static(path)
            except RequestError as error:
                self._json(error.status, {"error": str(error)})
            except (UnicodeDecodeError, ValueError):
                self._json(HTTPStatus.BAD_REQUEST, {"error": "요청 경로가 잘못됐습니다"})

        def _body(self) -> dict:
            raw_length = self.headers.get("Content-Length", "0")
            if self.headers.get("Transfer-Encoding") or not re.fullmatch(r"[0-9]{1,10}", raw_length):
                raise RequestError(HTTPStatus.BAD_REQUEST, "요청 길이가 잘못됐습니다")
            length = int(raw_length)
            if length > MAX_BODY_BYTES:
                raise RequestError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "요청이 너무 큽니다")
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, UnicodeDecodeError):
                raise RequestError(HTTPStatus.BAD_REQUEST, "JSON이 아닙니다") from None
            if not isinstance(body, dict):
                raise RequestError(HTTPStatus.BAD_REQUEST, "JSON 객체가 필요합니다")
            return body

        def do_POST(self):
            try:
                path = urlparse(self.path).path
                body = self._body()
                if path == "/session":
                    person_id = body.get("person_id")
                    if "person_id" in body and not isinstance(person_id, str):
                        raise RequestError(HTTPStatus.BAD_REQUEST, "인물 ID가 잘못됐습니다")
                    run = state.new_run(person_id)
                    self._json(HTTPStatus.OK, {"run_id": run.run_id, "person_id": run.runtime.profile.person_id})
                elif path == "/segments":
                    run_id, index, retry = body.get("run_id"), body.get("index"), body.get("retry", False)
                    if not isinstance(run_id, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", run_id):
                        raise RequestError(HTTPStatus.BAD_REQUEST, "run_id가 잘못됐습니다")
                    if type(index) is not int or index < 0 or type(retry) is not bool:
                        raise RequestError(HTTPStatus.BAD_REQUEST, "구간 번호 또는 재시도 값이 잘못됐습니다")
                    status = state.enqueue(run_id, index, retry=retry)
                    self._json(status, {"status": status.phrase})
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
            except RequestError as error:
                self._json(error.status, {"error": str(error)})

        def _events(self, run_id: str, after: int) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            try:
                while True:
                    events = state.wait_events(run_id, after, SSE_KEEPALIVE_SECONDS)
                    if events is None:
                        stale = json.dumps({"kind": "stale", "run_id": run_id, "event_id": after + 1})
                        self.wfile.write(f"id: {after + 1}\nevent: stale\ndata: {stale}\n\n".encode("utf-8"))
                        self.wfile.flush()
                        return
                    if not events:
                        self.wfile.write(b": keepalive\n\n")
                    for event in events:
                        after = event.payload["event_id"]
                        self.wfile.write(f"id: {after}\ndata: {event.to_json()}\n\n".encode("utf-8"))
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                return

    return Handler


def build_store(config: demo.DemoConfig):
    if config.store == "gbrain":
        from gbrain_store import GbrainStore

        return GbrainStore(config.past_takes())
    return demo.MemoryStore(config.past_takes())


def build_judge(config: demo.DemoConfig):
    if config.judge == "codex":
        home = prepare_codex_home(config)
        judge = demo.CodexJudge(model=config.judge_model, timeout=config.judge_timeout, codex_home=home)
        return judge, home is not None
    return demo.OpenAIJudge(model=config.judge_model, timeout=config.judge_timeout), demo.OpenAIJudge.ready()


def prepare_codex_home(config: demo.DemoConfig) -> Path | None:
    """프로필별 최소 Codex 홈을 만들고 프로세스 환경은 변경하지 않는다."""
    source = Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    auth = source / "auth.json"
    if not auth.is_file():
        return None
    identity = f"{config.base_dir.resolve()}\0{config.judge_model}\0{config.judge_effort}"
    home = BASE_DIR / ".codex-home" / hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = home / "auth.json"
    shutil.copy2(auth, target)
    target.chmod(0o600)
    lines = [f'model = "{config.judge_model}"', f'model_reasoning_effort = "{config.judge_effort}"']
    user_config = source / "config.toml"
    if user_config.is_file():
        for raw in user_config.read_text(encoding="utf-8").splitlines():
            key = raw.split("=", 1)[0].strip()
            if key in ("model_catalog_json", "openai_base_url", "model_provider"):
                lines.append(raw.strip())
            elif raw.startswith("["):
                break
    lines += ["", "[features]", "memories = false", "hooks = false", ""]
    (home / "config.toml").write_text("\n".join(lines), encoding="utf-8")
    return home


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--config", type=Path)
    args = parser.parse_args()
    catalog = profiles.load_catalog(BASE_DIR, config_path=args.config)
    state = DemoState(catalog)
    server = ThreadingHTTPServer((BIND_HOST, args.port), make_handler(state))
    server.daemon_threads = True
    print(f"http://{BIND_HOST}:{args.port}  people={len(catalog.people)} default={catalog.default_person_id}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
        state.close()


if __name__ == "__main__":
    main()
