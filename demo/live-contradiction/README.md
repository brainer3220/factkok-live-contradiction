# 팩트콕 · 발언을 원문으로 비교하기

인물을 고르고 영상을 재생하면 같은 인물의 과거 발언을 gbrain에서 검색하고 Codex의 OpenAI 모델로 비교한다. 결과 옆에서 인용문, 날짜, 매체와 원문 링크를 확인한다.

MagicPath의 **인물 메인 B · 포트레이트**와 기존 2단 상세를 실제 Python 웹 서버에 연결했다. 사진 목록에서 이재명 3개 주제와 한동훈 1개 주제를 실행할 수 있다. 나머지 6명은 `자료 연결 전`으로 표시한다. 실행 가능 여부와 기록 수는 서버가 확인한 파일에서 계산한다.

## 빠른 시작

Git 체크아웃, Python 3.11+, uv, 로그인된 Codex CLI, gbrain, FFmpeg와 ffprobe가 필요하다. Codex CLI의 저장소 검사를 유지하므로 ZIP 폴더 대신 git clone한 폴더에서 실행한다. 영상을 새로 준비할 때는 `drawtext` 필터와 한국어 TTF/TTC 폰트도 필요하다. 설치된 도구와 모델 사용 권한을 사용하며 설치·로그인을 자동 수행하지 않는다.

```bash
git clone --branch codex/end-to-end-demo https://github.com/brainer3220/factkok-live-contradiction.git
cd factkok-live-contradiction
uv sync --extra dev
codex login status
uv run python demo/live-contradiction/start_demo.py --prepare-media
```

브라우저에서 표시된 [로컬 데모](http://127.0.0.1:8765)를 연다. 첫 미디어 준비에는 수분이 걸릴 수 있다. 공식 방송사 기사에서 공개 플레이어 주소를 읽고 필요한 발언 구간만 로컬 영상으로 만든다. 방송 MP4는 저장소에 포함하지 않는다. 이미 두 영상이 있으면 다음 실행에는 `--prepare-media`가 필요 없다.

```bash
uv run python demo/live-contradiction/start_demo.py
```

시작 도구는 데모 폴더의 `.gbrain`에 PGLite를 초기화하고 두 사람의 과거 발언을 저장한 뒤 127.0.0.1에서만 서버를 연다. 전역 gbrain이나 Codex MCP 설정은 변경하지 않는다. `LIVE_DEMO_GBRAIN_HOME`으로 별도 저장 위치를 지정할 수 있다. Codex 인증은 기존 로그인에서 가져오고, ignored `.codex-home`의 인물별 최소 설정으로 child 프로세스를 실행한다.

## 데모 순서

1. 목록에서 이재명을 선택하고 영상 재생 버튼을 누른다.
2. 발언이 끝나면 실제 검색과 OpenAI 비교 상태가 표시된다.
3. 오른쪽에서 자동 예비 판정, 과거 인용문과 원문을 확인한다. 주제를 누르면 해당 구간으로 이동한다.
4. `인물`로 돌아가 한동훈을 선택한다. 별도 인물·영상·자막·run으로 비교한다.
5. `처음부터 다시 보기`는 결과를 비우고 새 run을 시작한다. 처리 실패는 원문을 유지하고 재시도를 제공한다.

실제 발언 뒤에는 판정 대기용 정지 화면과 무음이 있다. 이재명 영상은 30초씩 3개 슬롯, 한동훈 영상은 55초다. 편집 사실을 영상과 자막에서 알린다. 이재명 출처와 해석 범위는 [REAL_DEMO.md](REAL_DEMO.md), 한동훈은 [자료 기록](profiles/han-dong-hoon-sources.md)에 있다.

## 검증

```bash
uv run --extra dev pytest -q
node --test demo/live-contradiction/web/model.test.mjs
uv run python demo/live-contradiction/prepare.py check
uv run python demo/live-contradiction/prepare.py check --config demo/live-contradiction/profiles/han-dong-hoon.yaml
uv run python demo/live-contradiction/verify_demo.py
uv run python demo/live-contradiction/verify_control.py
```

두 verify 명령은 **실제 Codex 사용량을 사용한다**. `verify_demo.py`는 실행 중인 서버에 인물별 세그먼트를 보내고 실제 gbrain 검색·Codex 시작과 완료·카드·출처 일치·영상 Range 206을 검사한다. 브라우저 재생 검사는 별도다. [검증 기록](../../docs/DEMO_VERIFICATION.md)과 [제출 초안](../../docs/SUBMISSION.md)에 실행 범위를 남긴다.

## 구현

| 파일 | 책임 |
| --- | --- |
| `people.json`, `catalog.py` | 인물 ID와 검증된 설정·자막·영상 연결 |
| `index.html`, `web/` | 사진 목록, 검색, 2단 상세, 실제 재생·비교 상태 |
| `server.py` | 인물별 API, run 격리, 중복 방지, SSE 복원, 영상 Range |
| `pipeline.py`, `gbrain_store.py` | 같은 화자 검색, Codex 판정, 출처 검증 |
| `start_demo.py`, `build_media.py`, `prepare.py` | 시작, 미디어 준비, 사전 검사 |
| `verify_demo.py`, `verify_control.py`, `tests/` | 실제 provider 통합 검사와 오류·격리 회귀 검사 |

MagicPath 원본의 마크업·CSS·사진을 기존 정적 HTML/ES module 구조로 옮겼다. React 빌드 단계는 추가하지 않았다. [이식 기록](../../docs/MAGICPATH_IMPORT.md)에 기준 리비전을 남긴다. 사진 라이선스와 폰트 OFL은 `web/assets/`와 화면 하단 출처에서 확인할 수 있다.

## 한계

- 녹화 영상과 원문에 정렬한 자막으로 재현한다. 실시간 ASR 서비스가 아니다.
- 판정 지연은 실제 provider 응답에 따라 달라진다. 결과를 미리 넣거나 실패를 성공으로 바꾸지 않는다.
- `expected_relation`은 리허설 채점용이며 모델과 브라우저에 전달하지 않는다. 카드 인용문과 출처는 검색에서 선택한 저장 기록에서 가져온다.
- 입장 변화는 거짓말 판정이 아니다. 원문과 조건·시점·상황을 함께 확인해야 한다. 에이전트의 원문 대조는 사람 최종 검수나 재배포 허가를 뜻하지 않는다.
- 로컬 데모 검증은 운영 배포, 독립 정확도 평가, 행사 제출 완료를 뜻하지 않는다.
