"""gbrain을 기억 저장소로 쓰는 BrainStore.

과거 발언 하나를 gbrain 페이지 하나(slug: live-demo/<statement_id>)로 저장하고,
`gbrain search --where {"holder": ...}`로 같은 인물의 발언만 찾는다. gbrain 검색 결과에는
프론트매터가 없으므로 날짜·매체·출처 URL은 demo.yaml에서 statement_id로 붙인다.
측정(2026-11-09, PGLite·키워드 검색): 검색 1회 약 0.3초.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pipeline as demo

SLUG_PREFIX = "live-demo/"
SEARCH_TIMEOUT_SECONDS = 1.5
DEFAULT_GBRAIN_HOME = Path.home() / ".factkok-live-demo-gbrain"


def gbrain_env() -> dict[str, str]:
    home = os.getenv("LIVE_DEMO_GBRAIN_HOME", str(DEFAULT_GBRAIN_HOME))
    return {**os.environ, "GBRAIN_HOME": home}


def page_markdown(take: demo.Take) -> str:
    frontmatter = {
        "title": f"{take.holder} {take.date}",
        "type": "statement",
        "holder": take.holder,
        "statement_id": take.statement_id,
        "date": take.date,
        "venue": take.venue,
        "source_url": take.source_url,
    }
    lines = ["---", *(f"{key}: {json.dumps(value, ensure_ascii=False)}" for key, value in frontmatter.items()), "---"]
    return "\n".join(lines) + "\n" + take.text + "\n"


def parse_search(raw: str, holder: str, by_statement: dict[str, demo.Take]) -> list[demo.Take]:
    """gbrain search --json 결과를 Take로 바꾼다. 데모 slug와 등록된 statement만 받는다."""
    takes: list[demo.Take] = []
    seen: set[str] = set()
    for row in json.loads(raw):
        slug = str(row.get("slug", ""))
        if not slug.startswith(SLUG_PREFIX):
            continue
        statement_id = slug[len(SLUG_PREFIX) :]
        known = by_statement.get(statement_id)
        if known is None or statement_id in seen or known.holder != holder:
            continue
        seen.add(statement_id)
        takes.append(
            demo.Take(
                take_id=statement_id,
                statement_id=statement_id,
                holder=holder,
                text=str(row.get("chunk_text") or known.text),
                date=known.date,
                source_url=known.source_url,
                venue=known.venue,
            )
        )
    return takes


class GbrainStore:
    """gbrain CLI를 호출하는 저장소. 실패·시간 초과는 StoreError로 올린다."""

    label = "gbrain"

    def __init__(self, known_takes: list[demo.Take], *, binary: str = "gbrain"):
        self._binary = binary
        self._by_statement = {take.statement_id: take for take in known_takes}

    def search(self, holder: str, text: str, k: int) -> list[demo.Take]:
        command = [
            self._binary,
            "search",
            text,
            "--where",
            json.dumps({"holder": holder}, ensure_ascii=False),
            "--limit",
            str(k),
            "--json",
        ]
        try:
            completed = subprocess.run(
                command, capture_output=True, text=True, timeout=SEARCH_TIMEOUT_SECONDS, env=gbrain_env(), check=False
            )
        except subprocess.TimeoutExpired:
            raise demo.StoreError("gbrain 검색 시간 초과") from None
        except OSError as error:
            raise demo.StoreError(f"gbrain 실행 실패: {type(error).__name__}") from None
        if completed.returncode != 0:
            raise demo.StoreError(f"gbrain 검색 실패(exit {completed.returncode})")
        try:
            return parse_search(completed.stdout, holder, self._by_statement)
        except (json.JSONDecodeError, TypeError, AttributeError):
            raise demo.StoreError("gbrain 검색 결과 형식 오류") from None

    def seed(self, takes: list[demo.Take]) -> int:
        """과거 발언을 페이지로 저장한다. 같은 slug에 다시 쓰므로 여러 번 실행해도 중복이 없다."""
        for take in takes:
            completed = subprocess.run(
                # --force: demo.yaml이 단일 원천이므로 같은 slug를 의도적으로 덮어쓴다.
                [self._binary, "put", f"{SLUG_PREFIX}{take.statement_id}", "--force"],
                input=page_markdown(take),
                capture_output=True,
                text=True,
                timeout=30,
                env=gbrain_env(),
                check=False,
            )
            if completed.returncode != 0:
                raise demo.StoreError(f"gbrain put 실패: {take.statement_id}")
        return len(takes)
