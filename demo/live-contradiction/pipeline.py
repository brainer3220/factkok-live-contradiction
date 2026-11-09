"""말 바꾸기 즉시 소환 데모의 판정 파이프라인.

자막 세그먼트 하나를 입력받아 화면에 보낼 이벤트 목록을 돌려준다. HTTP와 분리되어
있어 pytest로 직접 검증할 수 있다. 카드의 인용문은 세그먼트 원문에서, 과거 발언의
날짜·매체·원문 링크는 검색으로 받은 take에서만 가져온다. LLM 출력은 어느 take를
가리키는지와 관계 판정에만 쓴다.
"""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Protocol

import yaml

from korean_politics_news_engine.embeddings import tokenize

RELATIONS = ("contradiction", "position_change", "consistent", "unrelated")
CARD_RELATIONS = ("contradiction", "position_change")
CARD_TITLES = {
    "contradiction": "다르게 말한 기록이 있음",
    "position_change": "입장이 바뀐 기록이 있음",
}
DEFAULT_THRESHOLD = 0.7
JUDGE_TIMEOUT_SECONDS = 5
SEARCH_LIMIT = 5

VERDICT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["relation", "confidence", "rationale", "past_take_id"],
    "properties": {
        "relation": {"type": "string", "enum": list(RELATIONS)},
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
        "past_take_id": {"type": "string"},
    },
}


class ConfigError(ValueError):
    """demo.yaml이 데모 계약을 만족하지 않는다."""


class StoreError(RuntimeError):
    """기억 저장소 검색이 실패했거나 시간을 넘겼다."""


class JudgeError(RuntimeError):
    """판정기 호출이 실패했거나 시간을 넘겼다."""


class VerdictError(ValueError):
    """판정 응답이 형식 또는 출처 검증을 통과하지 못했다."""


@dataclasses.dataclass(frozen=True)
class Take:
    """기억 저장소에 있는 과거 발언 하나."""

    take_id: str
    statement_id: str
    holder: str
    text: str
    date: str
    source_url: str
    venue: str


@dataclasses.dataclass(frozen=True)
class Segment:
    """transcript.json의 자막 세그먼트 하나."""

    index: int
    start: float
    end: float
    speaker: str
    text: str


@dataclasses.dataclass(frozen=True)
class Verdict:
    """검증을 통과한 판정."""

    relation: str
    confidence: float
    rationale: str
    take: Take


@dataclasses.dataclass(frozen=True)
class DemoConfig:
    """demo.yaml을 읽은 결과. holder는 이 파일 한 곳에만 둔다."""

    holder: str
    video: str
    store: str
    judge: str
    threshold: float
    statements: tuple[dict[str, Any], ...]
    base_dir: Path
    judge_model: str = "anthropic/claude-sonnet-5-5"
    judge_effort: str = "low"
    judge_timeout: int = 30

    def past_takes(self) -> list[Take]:
        """과거 발언만 take로 만든다. 지금 발언과의 짝 정보는 담지 않는다."""
        takes = []
        for statement in self.statements:
            past = statement["past"]
            takes.append(
                Take(
                    take_id=str(statement["statement_id"]),
                    statement_id=str(statement["statement_id"]),
                    holder=self.holder,
                    text=str(past["text"]),
                    date=str(past["date"]),
                    source_url=str(past["source_url"]),
                    venue=str(past.get("venue", "")),
                )
            )
        return takes


def load_config(path: Path) -> DemoConfig:
    """demo.yaml을 읽고 필수 필드를 검사한다."""
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    holder = str(data.get("holder", "")).strip()
    if not holder:
        raise ConfigError("holder가 비어 있습니다.")
    statements = data.get("statements") or []
    if not isinstance(statements, list) or not statements:
        raise ConfigError("statements가 비어 있습니다.")
    seen: set[str] = set()
    for statement in statements:
        statement_id = str(statement.get("statement_id", "")).strip()
        if not statement_id or statement_id in seen:
            raise ConfigError(f"statement_id가 없거나 중복됩니다: {statement_id!r}")
        seen.add(statement_id)
        past = statement.get("past") or {}
        for key in ("text", "date", "source_url"):
            if not str(past.get(key, "")).strip():
                raise ConfigError(f"{statement_id}: past.{key}가 비어 있습니다.")
        if not str(past["source_url"]).startswith(("https://", "http://")):
            raise ConfigError(f"{statement_id}: past.source_url이 URL이 아닙니다.")
    store = str(data.get("store", "memory"))
    if store not in ("memory", "gbrain"):
        raise ConfigError(f"store는 memory 또는 gbrain이어야 합니다: {store!r}")
    judge = str(data.get("judge", "codex"))
    if judge not in ("openai", "codex"):
        raise ConfigError(f"judge는 openai 또는 codex여야 합니다: {judge!r}")
    threshold = float(data.get("threshold", DEFAULT_THRESHOLD))
    if not 0.0 <= threshold <= 1.0:
        raise ConfigError("threshold는 0~1이어야 합니다.")
    judge_timeout = int(data.get("judge_timeout", 30))
    if not 1 <= judge_timeout <= 120:
        raise ConfigError("judge_timeout은 1~120초여야 합니다.")
    return DemoConfig(
        holder=holder,
        video=str(data.get("video", "demo.mp4")),
        store=store,
        judge=judge,
        threshold=threshold,
        statements=tuple(statements),
        base_dir=path.parent,
        judge_model=str(data.get("judge_model", "anthropic/claude-sonnet-5-5")),
        judge_effort=str(data.get("judge_effort", "low")),
        judge_timeout=judge_timeout,
    )


def load_transcript(path: Path) -> list[Segment]:
    """transcript.json을 읽는다. index는 0부터 빈틈없이 이어져야 한다."""
    rows = json.loads(path.read_text(encoding="utf-8"))
    segments = []
    for position, row in enumerate(rows):
        segment = Segment(
            index=int(row["index"]),
            start=float(row["start"]),
            end=float(row["end"]),
            speaker=str(row["speaker"]),
            text=str(row["text"]),
        )
        if segment.index != position or segment.end < segment.start:
            raise ConfigError(f"transcript 세그먼트 {position}의 index 또는 시간이 잘못됐습니다.")
        segments.append(segment)
    return segments


class BrainStore(Protocol):
    """과거 발언 검색 계약."""

    label: str

    def search(self, holder: str, text: str, k: int) -> list[Take]:
        """holder의 과거 발언 중 text와 가까운 것을 최대 k개 돌려준다."""


def bigram_similarity(a: str, b: str) -> float:
    """어절 안 글자 2-gram Jaccard 유사도.

    한국어는 조사가 붙어 어절 단위 비교(embeddings.text_similarity)로는
    '국토보유세는'과 '국토보유세를'이 다른 단어가 된다.
    """

    def grams(text: str) -> set[str]:
        result: set[str] = set()
        for token in tokenize(text):
            result.update(token[i : i + 2] for i in range(max(len(token) - 1, 1)))
        return result

    left, right = grams(a), grams(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


class MemoryStore:
    """gbrain을 쓸 수 없을 때 설정으로만 고르는 로컬 대체 저장소."""

    label = "로컬 대체"

    def __init__(self, takes: list[Take]):
        self._takes = list(takes)

    def search(self, holder: str, text: str, k: int) -> list[Take]:
        candidates = [take for take in self._takes if take.holder == holder]
        candidates.sort(key=lambda take: bigram_similarity(text, take.text), reverse=True)
        return candidates[:k]


class Judge(Protocol):
    """지금 발언과 과거 발언 후보를 비교해 JSON 문자열을 돌려준다."""

    def judge(self, prompt: str) -> str:
        """판정 JSON 원문을 돌려준다. 실패하면 JudgeError를 던진다."""


class OpenAIJudge:
    """OpenAI Chat Completions를 구조화 출력으로 호출한다."""

    endpoint = "https://api.openai.com/v1/chat/completions"

    def __init__(self, *, model: str | None = None, timeout: float = JUDGE_TIMEOUT_SECONDS):
        self._model = model or os.getenv("LIVE_DEMO_OPENAI_MODEL", "gpt-4.1-mini")
        self._timeout = timeout

    @staticmethod
    def ready() -> bool:
        return bool(os.getenv("OPENAI_API_KEY"))

    def judge(self, prompt: str) -> str:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise JudgeError("판정기 자격 증명이 없습니다.")
        body = {
            "model": self._model,
            "messages": [{"role": "user", "content": prompt}],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "verdict", "strict": True, "schema": VERDICT_SCHEMA},
            },
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as error:
            raise JudgeError(f"판정 API 오류 HTTP {error.code}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            raise JudgeError(f"판정 API 연결 실패: {type(error).__name__}") from None
        try:
            return str(payload["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError):
            raise JudgeError("판정 API 응답 형식이 예상과 다릅니다.") from None


class CodexJudge:
    """기존 run_codex_json_prompt를 재사용하는 codex 판정기(R8).

    Codex 로그인 사용량으로 판정한다. codex_home을 주면 MCP·플러그인·스킬이 없는 최소
    설정으로 실행해 시작 지연을 줄인다(측정: 기본 설정 22~39초 → 최소 설정 13~15초).
    """

    def __init__(
        self,
        *,
        codex: str | None = None,
        model: str = "",
        timeout: int = JUDGE_TIMEOUT_SECONDS,
        codex_home: Path | None = None,
    ):
        self._codex = codex or os.getenv("LIVE_DEMO_CODEX_BIN", "codex")
        self._model = model or os.getenv("LIVE_DEMO_CODEX_MODEL", "")
        self._timeout = timeout
        if codex_home is not None:
            # prepare_codex_home()가 CODEX_HOME에서 auth.json·config.toml을 복사한다.
            os.environ["CODEX_HOME"] = str(codex_home)

    @staticmethod
    def ready() -> bool:
        return True

    def judge(self, prompt: str) -> str:
        from korean_politics_news_engine.youtube_factcheck import run_codex_json_prompt

        try:
            return run_codex_json_prompt(
                self._codex,
                prompt,
                VERDICT_SCHEMA,
                prefix="live-contradiction-",
                output_filename="verdict.json",
                codex_model=self._model,
                timeout=self._timeout,
            )
        except subprocess.TimeoutExpired:
            raise JudgeError("판정 시간 초과") from None
        except (RuntimeError, OSError) as error:
            raise JudgeError(f"codex 판정 실패: {type(error).__name__}") from None


def build_prompt(holder: str, segment_text: str, takes: list[Take]) -> str:
    """판정 프롬프트. 과거 발언은 id와 함께 넣고 그중 하나만 고르게 한다."""
    lines = [
        "너는 한국어 정치 발언 비교 도우미다. 단정하지 말고 근거만 본다.",
        f"화자: {holder}",
        f"지금 발언: {segment_text}",
        "같은 화자의 과거 발언 후보:",
    ]
    for take in takes:
        lines.append(f"- id={take.take_id} | {take.date} | {take.venue} | {take.text}")
    lines.extend(
        [
            "지금 발언과 가장 관련 있는 과거 발언 하나를 id로 고르고 관계를 판정하라.",
            "relation: contradiction(서로 반대), position_change(입장이 바뀜), "
            "consistent(같은 입장), unrelated(관련 없음).",
            "confidence는 0~1. rationale은 한국어 한두 문장으로 두 발언의 차이를 설명한다.",
        ]
    )
    return "\n".join(lines)


def parse_verdict(raw: str, takes: list[Take], holder: str) -> Verdict:
    """판정 JSON을 검증한다. 검색 결과 밖의 take나 다른 화자의 take는 거부한다."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        raise VerdictError("판정 응답이 JSON이 아닙니다.") from None
    if not isinstance(data, dict):
        raise VerdictError("판정 응답이 객체가 아닙니다.")
    relation = data.get("relation")
    if relation not in RELATIONS:
        raise VerdictError(f"알 수 없는 relation: {relation!r}")
    confidence = data.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise VerdictError("confidence가 숫자가 아닙니다.")
    if not 0.0 <= float(confidence) <= 1.0:
        raise VerdictError("confidence가 0~1 밖입니다.")
    by_id = {take.take_id: take for take in takes}
    take = by_id.get(str(data.get("past_take_id", "")))
    if take is None:
        raise VerdictError("past_take_id가 검색 결과에 없습니다.")
    if take.holder != holder:
        raise VerdictError("past_take_id의 화자가 다릅니다.")
    rationale = str(data.get("rationale", "")).strip()
    return Verdict(relation=relation, confidence=float(confidence), rationale=rationale, take=take)


@dataclasses.dataclass(frozen=True)
class Event:
    """화면이나 로그로 보낼 이벤트."""

    kind: str  # card | log | banner | banner_clear
    run_id: str
    payload: dict[str, Any]

    def to_json(self) -> str:
        return json.dumps({"kind": self.kind, "run_id": self.run_id, **self.payload}, ensure_ascii=False)


class Pipeline:
    """run 하나에 속한 판정 상태. 세그먼트 하나를 받아 이벤트를 돌려준다."""

    def __init__(self, config: DemoConfig, store: BrainStore, judge: Judge, run_id: str):
        self._config = config
        self._store = store
        self._judge = judge
        self.run_id = run_id
        self._banner_active = False

    def _event(self, kind: str, **payload: Any) -> Event:
        return Event(kind=kind, run_id=self.run_id, payload=payload)

    def process(self, segment: Segment) -> list[Event]:
        holder = self._config.holder
        if segment.speaker != holder:
            return [self._event("log", segment_index=segment.index, message="화자 제외")]

        events: list[Event] = []
        try:
            takes = self._store.search(holder, segment.text, SEARCH_LIMIT)
        except StoreError as error:
            if not self._banner_active:
                self._banner_active = True
                events.append(self._event("banner", message="기억 저장소에 연결하지 못했습니다"))
            events.append(self._event("log", segment_index=segment.index, message=f"검색 실패: {error}"))
            return events
        if self._banner_active:
            self._banner_active = False
            events.append(self._event("banner_clear"))
        events.append(
            self._event(
                "log",
                segment_index=segment.index,
                message="검색 완료",
                query=segment.text,
                take_ids=[take.take_id for take in takes],
            )
        )
        if not takes:
            events.append(self._event("log", segment_index=segment.index, message="비교할 과거 발언 없음"))
            return events

        try:
            raw = self._judge.judge(build_prompt(holder, segment.text, takes))
            verdict = parse_verdict(raw, takes, holder)
        except (JudgeError, VerdictError) as error:
            events.append(
                self._event("log", segment_index=segment.index, message=f"판정을 완료하지 못했습니다: {error}")
            )
            return events

        summary = {
            "segment_index": segment.index,
            "relation": verdict.relation,
            "confidence": round(verdict.confidence, 2),
            "statement_id": verdict.take.statement_id,
        }
        if verdict.relation in CARD_RELATIONS and verdict.confidence >= self._config.threshold:
            events.append(
                self._event(
                    "card",
                    **summary,
                    title=CARD_TITLES[verdict.relation],
                    now_text=segment.text,
                    now_seconds=segment.start,
                    past_text=verdict.take.text,
                    past_date=verdict.take.date,
                    past_venue=verdict.take.venue,
                    past_url=verdict.take.source_url,
                    rationale=verdict.rationale,
                )
            )
        else:
            events.append(self._event("log", **summary, message="카드 기준 미달"))
        return events
