# 실행 가능한 데모 통합 계획

사용자의 목표는 인물을 선택해 영상을 재생하고 실제 gbrain 검색과 Codex의 OpenAI 판정을 거쳐 출처를 확인하는 데모를 만드는 것이다. MagicPath B 메인과 기존2단 상세를 실제 코드에 이식하고 제출 설명·GitHub 링크를 준비한다.

## 작업 목록

- [x] Read the Principles section of the poteto-mode skill.
- [x] Phase A: Frame.
- [x] Phase B: Design the workflow.
- [x] Phase C: Run the loop.
- [x] 기존 실제 데이터·OpenAI 모델·검색 개선을 보존하고 baseline 검사 결과를 남긴다.
- [x] 인물 ID와 설정·자막·영상·run을 함께 묶는 서버 계약을 구현하고 HTTP 회귀 검사를 통과시킨다.
- [x] MagicPath B의 사진 메인과 기존2단 상세를 정적 웹 UI로 이식하고 실제 SSE·재생 상태에 연결한다.
- [x] 두 번째 인물의 출처·영상·자막을 준비한다. 원문과 영상이 확인된 자료만 실행 가능한 인물로 노출한다.
- [x] 실제 gbrain과 Codex 호출을 완료하고 같은 run의 UI·검색·모델·카드를 대조한다.
- [x] 브라우저에서 선택·재생·판정·출처·재시작·실패 복구·모바일을 확인한다.
- [ ] 재현 명령, 제출 설명,90초 데모 자료,GitHub 변경 링크를 준비한다.
- [x] Phase D: Keep the audit trail.
- [ ] Phase E: Verify and hand back.

## 완료 판정

- 사진 메인에서 연결된 인물을 선택하고 실행한다. 생성한 run은 해당 인물의 설정·영상·자막·검색에만 연결된다.
- 실제 녹화 영상과 정렬 자막으로 검색·판정이 실행된다. 사전 판정이나 expected_relation을 화면 결과로 쓰지 않는다.
- 실제 gbrain 검색과 Codex OpenAI 호출의 시작·완료가 동일 run·segment에 기록되고 카드 원문·날짜가 선택한 take와 일치한다.
- 재접속해도 카드가 중복되지 않는다. 지난 run 결과가 새 인물 화면으로 들어오지 않는다.
- 영상 Range206/416,실패와 재시도,기록 미연결 상태,키보드·모바일 배치를 확인한다.
- 코드·실제 실행 증거·제출 설명이 같은 동작을 설명한다. 공개 저장소에 인증 정보·자동 생성 로그·방송 MP4를 올리지 않는다.

## 범위와 순서

대상은 별도 해커톤 저장소 factkok-live-contradiction이다. 운영 팩트콕의 owner,Postgres,Dagster,공개 홈은 변경하지 않는다. 기존 로컬 변경12파일을 별도 codex/end-to-end-demo worktree에 보존한다.

서버 계약,브라우저 UI,추가 인물 자료를 별도 파일 소유로 병렬 처리한다. Node 런타임과 React 빌드 단계를 새로 추가하지 않는다. 기존 Python 정적 서버에 MagicPath의 마크업·CSS·사진을 이식한다.

두 번째 인물은 실제 원문과 영상이 확인되는 경우에만 추가한다. 확인이 안 된 자료를 다른 인물의 run으로 돌리거나 가짜 판정으로 채우지 않는다.

## 데이터 계약

people.json의 people 배열은 id,name,portrait,선택적 config와 transcript 경로를 가진다. 경로는 서버가 소유하며 브라우저 응답에는 노출하지 않는다. ID로 profile을 조회하고,run이 해당 profile의 Pipeline과 segments를 보유한다. 공개 API는 expected_relation·인증·내부 경로를 반환하지 않는다.

GET /people은 id,name,portrait,status,선택 가능한 기록 메타데이터를 반환한다. GET /config,/transcript.json,/video는 person_id로 조회한다. POST /session은 person_id를 받고 run_id와 person_id를 반환한다. 기존 person_id 생략 호출은 기본 인물을 사용한다. POST /segments는 기존 run_id/index 계약을 유지한다.

## 검증 단계

provider 경계 mock을 쓴 HTTP 계약 테스트와 실제 provider 호출 검증을 구분한다. 전자는 오류·격리·재접속·Range의 회귀를 검사한다. 후자는 실제 검색·판정과 브라우저 결과를 대조한다. 두 결과를 합쳐 hosted 서비스나 실시간 ASR의 성공이라고 주장하지 않는다.

## 도구 적용

이 Codex 환경에는 poteto-agent,Comment Sicko,control-ui 전용 역할이 없다. explorer와 worker로 소유 범위를 나누고,Codex의 CUA 브라우저 도구로 실제 UI를 검증한다. pstack 모델 설정의 사용 가능한 GPT 역할을 사용한다. 지원되지 않는 모델 가족을 썼다고 주장하지 않는다.
