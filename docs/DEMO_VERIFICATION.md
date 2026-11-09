# 데모 검증 기록

2026-11-09 로컬 검증이다. 대상은 독립 해커톤 저장소 factkok-live-contradiction이다. 운영 팩트콕의 Postgres·Dagster·공개 서비스 배포는 변경하지 않았다.

## 실행 결과

| 검사 | 결과와 증거 |
| --- | --- |
| 전체 Python 회귀 | `.venv/bin/python -m pytest -q` · 170 passed (최종 전체 검사) |
| UI 상태 회귀 | `node --test demo/live-contradiction/web/model.test.mjs` · 12 passed |
| JavaScript 문법 | `node --check demo/live-contradiction/web/app.js` · PASS |
| 변경 공백 | `git diff --check` · PASS |
| 실제 HTTP·gbrain·Codex | 이재명3·한동훈1건 모두 검색·판정 완료, 선택된 원문·날짜·URL 일치, 영상 Range206 |
| 실제 일치 대조군 | 같은 과거 원문을 다시 입력 · 최종 consistent, confidence0.99, 카드0, 판정 completed. 관계 정답과 대조군 설명을 모델에 전달하지 않음 |
| 실제 브라우저 | 인물 선택·검색/빈상태/ESC·미연결·두 인물 재생/판정/출처·주제 이동·재시작·키보드 건너뛰기 확인 |
| 모바일 | 390px 목록2열·상세1열, 문서 scrollWidth390px 확인 |
| 실패·복구 | 탭 네트워크 차단으로 영상 오류와 원문 유지 확인, 복구 후 영상 다시 불러오기 확인 |
| 첫 실행 | 영상·DB·가상환경 없는 복사본에서 공식4구간 다운로드·편집 → PGLite 초기화·두 인물 seed → 서버 시작 PASS. Git 체크아웃 조건으로 실제 Codex4/4 재검증 PASS |

HTTP 테스트의 외부 모델 경계는 mock이며 실제 호출과 구분한다. macOS sandbox는 loopback bind/connect를 막아 해당 검사는 허용된 도구 실행으로 수행했다. 일반 sandbox의 socket PermissionError를 코드 통과로 표시하지 않았다.

실제 provider는 Codex CLI의 `gpt-6-luna`다. 첫 HTTP 실행의 run은 이재명 `0dca3ce37e72`, 한동훈 `04070046c54f`이며 이재명0/2/4와 한동훈0에서 search.end, judge.start, judge.end(completed), progress.completed를 확인했다. 판정 지연은 첫 실행14.6~20.1초, 브라우저 완주 실행19.2~22.3초였다. 결과가 표시되기 전에 성공 문구를 넣지 않았다.

## 재현

Python3.14.0, gbrain0.60.619.0, codex-cli0.167.0-alpha.3.2, macOS의 drawtext 지원 FFmpeg에서 확인했다.

```bash
uv sync --extra dev
uv run python demo/live-contradiction/start_demo.py --prepare-media
# 별도 터미널: 실제 Codex 사용량을 사용한다.
uv run python demo/live-contradiction/verify_demo.py
uv run python demo/live-contradiction/verify_control.py
```

이미 영상이 있으면 `--prepare-media`를 생략한다. `--check-only`는 도구·로그인·원문 URL·영상·자막을 검사하고 DB 초기화나 모델 호출을 하지 않는다. `prepare.py check --config .../han-dong-hoon.yaml`은 해당 인접 자막을 사용한다.

## 로컬 산출물

화면 산출물은 별도 demo-integration 폴더에 두었다. 인증·방송 MP4·생성 판정 로그는 GitHub 소스에서 제외했다.

- `people-desktop.png`, `people-mobile.png`: 실제 인물 목록.
- `lee-final-card.png`, `han-real-card.png`, `detail-mobile.png`: 실제 상세와 판정.
- `factkok-demo-90s-final.mp4`: H.264,1440x1000,90.000초,무음. 실제 페이지를 약2fps로 캡처해 원래 경과 순서로 인코딩했다. 마지막 카드 화면을90초까지 유지했다. 브라우저 영상의 원래 음성은 녹화하지 않았다.
- `full-screen-recording/frames.json`: 원본 캡처 시각과 파일명. 녹화 안의 판정도 실제 새 run에서 호출했다.
- ignored `demo/live-contradiction/logs/integration-http.json`, `negative-control-actual.json`과 각 run JSONL: 실제 provider·검색·출처 대조 증거.

브라우저에서 muted=false와 AAC 영상 재생을 확인했다. 실제 사람이 음성을 청취한 최종 검수나 실제 휴대폰 검사로 확대하지 않는다.

## 독립 리뷰와 수정

서버 담당자와 UI 담당자가 서로 구현하지 않은 파일을 읽고 경계 재현을 수행했다. 별도 모델 가족의 교차 리뷰는 이 환경에서 수행하지 않았다.

| 발견 | 수정·검증 |
| --- | --- |
| POST 응답 유실 뒤 이미 받은 카드가 queued로 덮임 | SSE progress/card도 서버 수락 증거로 저장, 두 회귀 추가 |
| 상세 건너뛰기 링크가 인물 URL을 제거함 | main focus/scroll만 수행, 실제 키보드 Enter로 인물 URL과 상세 유지 확인 |
| 인물 전환 후 오래된 검색이 새 모델 호출 시작 | judge 직전 run 유효성 확인, 실제 HTTP barrier 회귀에서 old judge0회 확인 |
| OpenAI 경로의 설정 모델과 요청 모델 불일치 | 설정 모델 전달, outbound request와 진행 이벤트 대조 |
| 편집 안내가 현재 발언·출처를 덮음 | 현재 statement 원문 유지, 최신 한동훈 재생의 대기/카드 상태에서 확인 |
| 현재 인용·영상 시점이 달라도 검사 도구가 PASS | 현재 transcript text/start까지 대조,27개 회귀 및 실제4카드 원본 JSONL 재대조 PASS |
| HLS 전체 다운로드가300초에 시간 초과 | 필요한 구간만 다운로드하도록 변경 |
| HLS 직접 seek가 ChannelA 첫약1초를 놓침 | 원본 시작보다10초 앞에서 입력 seek 후 출력 seek. 실제13초 구간이12.018초에서13.013초로 복구, 허용오차0.05초 유지 |

첫 실행 오류를 고치며 판정 결과나 길이 기준을 약화하지 않았다. 원본 manifest의 source_start/end는 유지하고 빌드용 임시 구간만0초 기준으로 정규화했다.

## 문서와 코드의 차이 정리

기존 문서의 전역 gbrain 초기화 명령은 데모의 저장 위치와 달랐다. 시작 도구에서 workspace의 PGLite 경로와 seed 환경을 함께 지정하도록 맞췄다. 단일 transcript를 고정한 사전 검사도 인물별 자막으로 맞췄다. REAL_DEMO.md의 개인 venv CA 경로와 이전 실행 경로는 제거하고 공통 시작 명령으로 정리했다. 인증서 검증은 유지한다. 한동훈 media.json의 해시는 최초 제작본 참조값임을 명시했다. 첫 실행의 구간 재인코딩 산출물과 바이트 일치를 약속하지 않는다.

## 적용 원칙과 남은 범위

Model the Domain에 따라 인물·설정·영상·자막을 profile로 묶고 run이 이를 고정한다. Make Operations Idempotent로 같은 세그먼트 중복과 명시적 재시도를 구분한다. Prove It Works와 Test Behavior, Not Implementation에 따라 실제 HTTP 응답·SSE·영상·provider 완료와 오류 복구를 확인했다.

NOT_RUN: 실시간 ASR, 실제 휴대폰, 운영 배포, 독립 판정 정확도 평가, 행사 제출과 영상 공개 업로드.
UNVERIFIED: 행사 공식 제출 폼·규정, 사람의 최종 인용 검수와 방송 재배포 허가. 현재 구현을 막는 확인된 코드 결함은 남아 있지 않다. 공개 업로드·행사 제출은 수행하지 않았다.

## 첫 실행 최종 결과

별도 소스 복사본에서 최초 영상·DB·venv가 없는 상태로 start_demo.py --prepare-media를 실행했다. 공식4구간의 길이는10.010/11.812/13.013/17.017초, 각 영상 트랙 시작은0~0.033초였다. 재생 영상은 이재명90.021초와 한동훈55.021초, 모두H.264와 스테레오AAC다. preflight, PGLite초기화,4개 과거기록 저장과 localhost서버 시작을 완료했다.

첫 provider 검사에서는 복사본의 Git 메타데이터를 생략한 탓에 Codex가 실행을 거부했다. 실제 오류가 Git 저장소 검사임을 분리 확인했다. 임시 Git 저장소를 초기화해 clone과 같은 조건으로 맞춘 뒤, 같은 서버에서 이재명 run a124e0d22b8c / 한동훈 run95e4aa7e1067의4개 호출·원문 대조·206을 모두 통과했다. read-only sandbox와 Codex의 Git 검사를 유지했고, 우회 옵션은 추가하지 않았다. README에도 git clone한 폴더에서 실행할 것을 명시했다. 실패 결과도 ignored cold-start-non-git-http.json에 보존했다.

최종 실제 결과는 cold-start-http.json에서 person/run/provider/model과 완료 이벤트,4개의 source_checks=true,issues=[]로 확인한다. 이 검사는 로컬 첫 실행의 연결 검증이며 hosted 서비스나 모델 정확도 평가가 아니다.

최종 출처 검사 보완 후에는 이미 보존된 실제4개 card JSONL을 check_card_source로 재대조했다. 과거 인용·날짜·URL뿐 아니라 현재 문장과0/30/60초 시점까지4/4일치했다. 결과는 ignored cold-start-source-audit.json에 있다. 이 대조를 새 모델 호출로 표시하지 않는다.

## 최종 인계

원래 /Users/brainer/Programming/factkok-live-contradiction에 소스51개와 두 로컬 영상을 반영하고 byte일치를 확인했다. 원래 Git index와 기존 변경은 보존했다. 수정 전 파일 백업은 /private/tmp/factkok-original-backup-20261109에 있다. 원래 저장소에서 기본 로컬 gbrain 경로로 verify_control.py를 실행해 consistent0.99·completed·카드0을 다시 확인했다. localhost:8765에서2명의 자료 검사·시딩·서버 시작과 브라우저 인물 선택·55초 영상 로딩을 확인했다. 임시8773/8774/8775 서버는 중지하고 최종8765를 유지했다.

GitHub 검토 브랜치 codex/end-to-end-demo와 초안 PR #2를 만들었다. 코드 커밋은 bd511f8이다. main병합·배포·행사 제출·영상 공개 업로드는 수행하지 않았다. 제출 설명과 검토 링크는 SUBMISSION.md에서 확인한다.
