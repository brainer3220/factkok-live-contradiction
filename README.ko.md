# 팩트콕 · 발언 비교 데모

[English](README.md) | **한국어**

인물을 선택해 영상을 재생하면 영상 속 발언을 같은 사람의 과거 발언과 날짜·원문 링크로 비교한다. gbrain에서 기록을 검색하고 Codex의 OpenAI 모델로 관계를 판정한다. 입장 변화는 거짓말 판정이 아니다.

[![데모 영상 — YouTube에서 재생](https://img.youtube.com/vi/e7cd_2SQ6Ks/hqdefault.jpg)](https://youtu.be/e7cd_2SQ6Ks)

[이전 데모 · 90초, 무음](https://youtu.be/3o-WRERS_u8)

## 로컬 실행

Git 체크아웃에서 실행한다. Python 3.11+, uv, gbrain, 로그인된 Codex CLI, FFmpeg/ffprobe가 필요하다. 영상을 준비할 때는 FFmpeg의 `drawtext` 필터와 한국어 폰트도 필요하다.

```bash
uv sync --extra dev
uv run python demo/live-contradiction/start_demo.py --prepare-media
```

[localhost:8765](http://127.0.0.1:8765)를 연다. 영상이 준비된 이후에는 `--prepare-media`를 생략한다. 녹화 영상과 정렬된 자막을 사용하는 데모이며 실시간 음성 인식을 수행하지 않는다.

## 관련 자료

- 데모 설명·실행법: [demo/live-contradiction/README.md](demo/live-contradiction/README.md)
- 테스트: `uv run --extra dev pytest tests -q`
- UI 상태 테스트: `node --test demo/live-contradiction/web/model.test.mjs`
- 제출 문구: [docs/SUBMISSION.md](docs/SUBMISSION.md)
- 검증 범위와 결과: [docs/DEMO_VERIFICATION.md](docs/DEMO_VERIFICATION.md)

이 저장소는 2026-11-09 해커톤에서 새로 만든 부분만 담는다. `korean_politics_news_engine/`은 팩트콕 비공개 엔진에서 데모가 쓰는 부분만 옮긴 것이다(`embeddings.py`와 `youtube_factcheck.py`의 Codex 실행 함수).
