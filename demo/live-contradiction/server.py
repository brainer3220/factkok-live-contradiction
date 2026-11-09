"""말 바꾸기 데모 서버. 127.0.0.1에서만 열린다.

실행: uv run python demo/live-contradiction/server.py [--port 8765]

판정 로직은 pipeline.py에 있고, 이 파일은 run 수명, 판정 대기열, SSE, 정적 파일만 맡는다.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import shutil
import sys
import threading
import time
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR.parents[1]))
sys.path.insert(0, str(BASE_DIR))

import pipeline as demo  # noqa: E402

BIND_HOST = "127.0.0.1"
QUEUE_LIMIT = 3
WORKERS = 2
MAX_BODY_BYTES = 4096
SSE_KEEPALIVE_SECONDS = 15


class Run:
    """재생 한 번. 이벤트는 run 안에만 쌓이고 다른 run으로 새지 않는다."""

    def __init__(self, run_id: str, pipe: demo.Pipeline):
        self.run_id = run_id
        self.pipeline = pipe
        self.events: list[demo.Event] = []
        self.sent_indexes: set[int] = set()


class DemoState:
    """현재 run, 판정 대기열, 작업 스레드를 가진다."""

    def __init__(self, config: demo.DemoConfig, segments: list[demo.Segment], store, judge, judge_ready: bool):
        self.config = config
        self.segments = segments
        self.store = store
        self.judge = judge
        self.judge_ready = judge_ready
        self.log_dir = BASE_DIR / "logs"
        self.log_dir.mkdir(exist_ok=True)
        self._lock = threading.Condition()
        self._queue: collections.deque[tuple[str, demo.Segment, float]] = collections.deque()
        self.current: Run | None = None
        for _ in range(WORKERS):
            threading.Thread(target=self._work, daemon=True).start()

    def new_run(self) -> Run:
        run_id = uuid.uuid4().hex[:12]
        run = Run(run_id, demo.Pipeline(self.config, self.store, self.judge, run_id))
        with self._lock:
            self._queue.clear()
            self.current = run
            self._lock.notify_all()
        self._log(run_id, {"message": "run 시작", "store": self.store.label, "judge": self.config.judge})
        if not self.judge_ready:
            self.emit(demo.Event("banner", run_id, {"message": "판정기 미설정", "sticky": True}))
        return run

    def enqueue(self, run_id: str, index: int) -> HTTPStatus:
        if not 0 <= index < len(self.segments):
            return HTTPStatus.BAD_REQUEST
        with self._lock:
            run = self.current
            if run is None or run.run_id != run_id:
                return HTTPStatus.CONFLICT
            if index in run.sent_indexes:
                return HTTPStatus.ACCEPTED
            run.sent_indexes.add(index)
            if not self.judge_ready:
                dropped = None
            elif len(self._queue) >= QUEUE_LIMIT:
                dropped = self._queue.popleft()
            else:
                dropped = None
            if self.judge_ready:
                self._queue.append((run_id, self.segments[index], time.time()))
                self._lock.notify()
        self._log(run_id, {"message": "세그먼트 수신", "segment_index": index, "speaker": self.segments[index].speaker})
        if dropped is not None:
            self.emit(
                demo.Event("log", dropped[0], {"segment_index": dropped[1].index, "message": "판정 생략(대기열 초과)"})
            )
        return HTTPStatus.ACCEPTED

    def _work(self) -> None:
        while True:
            with self._lock:
                while not self._queue:
                    self._lock.wait()
                run_id, segment, enqueued_at = self._queue.popleft()
                run = self.current
            if run is None or run.run_id != run_id:
                continue
            started = time.time()
            events = run.pipeline.process(segment)
            finished = time.time()
            self._log(
                run_id,
                {
                    "message": "판정 처리",
                    "segment_index": segment.index,
                    "queue_ms": int((started - enqueued_at) * 1000),
                    "process_ms": int((finished - started) * 1000),
                },
            )
            for event in events:
                self.emit(event)

    def emit(self, event: demo.Event) -> None:
        self._log(event.run_id, {"event": json.loads(event.to_json())})
        with self._lock:
            run = self.current
            if run is None or run.run_id != event.run_id:
                return  # 이전 run의 결과는 로그에만 남긴다.
            run.events.append(event)
            self._lock.notify_all()

    def wait_events(self, run_id: str, start: int, timeout: float) -> list[demo.Event] | None:
        """run_id의 start 이후 이벤트를 기다린다. run이 바뀌면 None."""
        with self._lock:
            deadline = time.time() + timeout
            while True:
                run = self.current
                if run is None or run.run_id != run_id:
                    return None
                if len(run.events) > start:
                    return run.events[start:]
                remaining = deadline - time.time()
                if remaining <= 0:
                    return []
                self._lock.wait(remaining)

    def _log(self, run_id: str, record: dict) -> None:
        record = {"ts": round(time.time(), 3), "run_id": run_id, **record}
        with (self.log_dir / f"{run_id}.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def make_handler(state: DemoState):
    class Handler(BaseHTTPRequestHandler):
        server_version = "LiveContradictionDemo/1"

        def log_message(self, format, *args):  # noqa: A002 - BaseHTTPRequestHandler 시그니처
            return

        def _json(self, status: HTTPStatus, body: dict) -> None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def _file(self, path: Path, content_type: str) -> None:
            if not path.is_file():
                self._json(HTTPStatus.NOT_FOUND, {"error": f"{path.name} 없음"})
                return
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(path.stat().st_size))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with path.open("rb") as handle:
                try:
                    shutil.copyfileobj(handle, self.wfile, 64 * 1024)
                except (BrokenPipeError, ConnectionResetError):
                    return

        def do_GET(self):  # noqa: N802 - http.server 규약
            url = urlparse(self.path)
            if url.path == "/":
                self._file(BASE_DIR / "index.html", "text/html; charset=utf-8")
            elif url.path == "/transcript.json":
                self._file(BASE_DIR / "transcript.json", "application/json; charset=utf-8")
            elif url.path == "/video":
                self._file(state.config.base_dir / state.config.video, "video/mp4")
            elif url.path == "/config":
                self._json(
                    HTTPStatus.OK,
                    {"holder": state.config.holder, "store_label": state.store.label, "judge_ready": state.judge_ready},
                )
            elif url.path == "/events":
                self._events(parse_qs(url.query).get("run_id", [""])[0])
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def do_POST(self):  # noqa: N802 - http.server 규약
            url = urlparse(self.path)
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY_BYTES:
                self._json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "요청이 너무 큽니다"})
                return
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "JSON이 아닙니다"})
                return
            if url.path == "/session":
                run = state.new_run()
                self._json(HTTPStatus.OK, {"run_id": run.run_id})
            elif url.path == "/segments":
                try:
                    status = state.enqueue(str(body.get("run_id", "")), int(body.get("index", -1)))
                except (TypeError, ValueError):
                    status = HTTPStatus.BAD_REQUEST
                self._json(status, {"status": status.phrase})
            else:
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})

        def _events(self, run_id: str) -> None:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            sent = 0
            try:
                while True:
                    events = state.wait_events(run_id, sent, SSE_KEEPALIVE_SECONDS)
                    if events is None:
                        self.wfile.write(b"event: stale\ndata: {}\n\n")
                        self.wfile.flush()
                        return
                    if not events:
                        self.wfile.write(b": keepalive\n\n")
                    for event in events:
                        self.wfile.write(f"data: {event.to_json()}\n\n".encode("utf-8"))
                    sent += len(events)
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
    return demo.OpenAIJudge(timeout=config.judge_timeout), demo.OpenAIJudge.ready()


def prepare_codex_home(config: demo.DemoConfig) -> Path | None:
    """판정 전용 최소 Codex 홈을 만든다. 로그인 정보가 없으면 None."""
    source = Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    auth = source / "auth.json"
    if not auth.is_file():
        return None
    home = BASE_DIR / ".codex-home"
    home.mkdir(mode=0o700, exist_ok=True)
    target = home / "auth.json"
    shutil.copy2(auth, target)
    target.chmod(0o600)
    lines = [
        f'model = "{config.judge_model}"',
        f'model_reasoning_effort = "{config.judge_effort}"',
    ]
    # 사용자 Codex 설정에서 모델 라우팅에 필요한 값만 옮긴다(MCP·플러그인·스킬 제외).
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
    parser.add_argument("--config", type=Path, default=BASE_DIR / "demo.yaml")
    args = parser.parse_args()
    config = demo.load_config(args.config)
    segments = demo.load_transcript(BASE_DIR / "transcript.json")
    judge, ready = build_judge(config)
    state = DemoState(config, segments, build_store(config), judge, ready)
    server = ThreadingHTTPServer((BIND_HOST, args.port), make_handler(state))
    server.daemon_threads = True
    print(f"http://{BIND_HOST}:{args.port}  store={state.store.label} judge={config.judge} ready={ready}")
    server.serve_forever()


if __name__ == "__main__":
    main()
