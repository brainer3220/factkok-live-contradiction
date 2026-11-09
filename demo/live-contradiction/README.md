# 팩트콕 · 말 바꾸기 즉시 소환

정치인이 영상에서 말하는 순간, 같은 사람이 예전에 다르게 말한 기록을 날짜와 원문 링크와 함께 옆에 띄운다.

## 왜 필요한가

정치 영상을 보다가 "저 사람 예전엔 반대로 말하지 않았나?" 싶을 때 지금은 검색창을 열고 기사를 뒤져야 한다. 실시간 팩트체크 도구는 이미 있다(예: X 게시물을 확인하는 SuperX). 하지만 결과를 쌓아 두지 않으면 "과거의 같은 사람"과 맞붙일 수 없다. 이 데모는 검증된 과거 발언을 기억 저장소에 쌓아 두고, 지금 발언과 바로 비교한다.

팩트콕은 정치 YouTube 링크를 주장별 근거 리포트로 만드는 서비스다. 이 기능은 리포트를 "보는 순간"으로 옮겨, 반복 사용과 구독 전환을 만드는 장면을 목표로 한다.

## 동작

```
영상 재생 ─▶ 자막 세그먼트 끝 ─▶ POST /segments ─▶ 화자 필터(데모 인물만)
                                              │
                         기억 저장소 검색(같은 인물의 과거 발언 k개)
                                              │
                    Codex 판정(모순·입장 변화·일치·무관 + 고른 과거 발언 id)
                                              │
               검증: 고른 id가 검색 결과에 있고 같은 인물인가 → 아니면 카드 없음
                                              │
                        SSE로 카드 전송 ─▶ 화면 오른쪽 칼럼
```

카드의 인용문은 자막 원문에서, 과거 발언의 날짜·매체·링크는 저장소 기록에서만 가져온다. LLM은 어느 과거 발언인지와 관계 판정만 한다. 카드는 "다르게 말한 기록이 있음"까지만 말하고, 자동 예비 판정임과 원문 확인을 함께 표시한다.

## 실행

```bash
uv sync --extra dev
gbrain init --pglite                                      # GBRAIN_HOME=~/.factkok-live-demo-gbrain 권장
uv run python demo/live-contradiction/prepare.py seed    # 과거 발언을 gbrain 페이지로 저장(반복 실행 안전)
uv run python demo/live-contradiction/prepare.py check   # 데이터·출처·영상 점검
uv run python demo/live-contradiction/server.py          # http://127.0.0.1:8765
```

- 기억 저장소는 gbrain(PGLite, 키워드 검색)이다. 과거 발언 하나가 페이지 하나이고 `holder` 프론트매터로 인물을 거른다. 검색 1회 약 0.3초(2026-11-09 측정). gbrain을 쓸 수 없으면 `demo.yaml`의 `store: memory`로 바꾸고, 화면에 "로컬 대체"가 표시된다.
- 판정은 로그인된 `codex` CLI 사용량으로 돈다(`demo.yaml`의 `judge: codex`). MCP·플러그인 없이 최소 설정으로 실행한다.
- 서버는 127.0.0.1에서만 열린다.
- 리허설 채점: `uv run python demo/live-contradiction/prepare.py score <run_id>`

## 오늘 만든 것

| 파일 | 내용 |
|---|---|
| `pipeline.py` | 화자 필터, 검색, Codex 판정, 출처 검증, 이벤트 생성 |
| `gbrain_store.py` | gbrain 시딩(페이지 upsert)과 인물별 검색 |
| `server.py` | run 수명, 판정 대기열(최대 3), SSE, 정적 파일 |
| `index.html` | 영상 + 과거 발언 카드 칼럼 + 판정 로그 |
| `prepare.py` | 사전검사(출처 URL·대조 기록·faststart), 리허설 채점 |
| `tests/test_live_contradiction_pipeline.py` | 카드 생성, 출처 검증 실패 7종, 라우팅, 검색 실패 |

가져다 쓴 기존 코드: `korean_politics_news_engine/embeddings.py`의 `tokenize`, `youtube_factcheck.py`의 `run_codex_json_prompt`.

## 한계

- 녹화 영상과 사람이 자른 자막으로 라이브를 흉내 낸다. 실시간 음성 인식은 다음 단계다.
- Codex 판정 한 번에 약 20~38초가 걸린다(2026-11-09 리허설 측정). 카드는 발언 뒤 그만큼 늦게 뜬다.
- 샘플 `demo.yaml`은 가상 인물이다. 실제 인물 데이터는 사람이 원문과 대조한 뒤 `verified_at`을 기록해야 `check`를 통과한다.
- 자동 판정은 참고용이다. 근거의 원문을 직접 확인해야 한다.
