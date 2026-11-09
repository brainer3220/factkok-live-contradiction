# 팩트콕 · 발언 비교 데모

인물을 선택해 영상을 재생하면 같은 사람의 과거 발언을 날짜와 원문 링크와 함께 비교한다. gbrain에서 기록을 검색하고 Codex의 OpenAI 모델로 관계를 판정한다. 입장 변화는 거짓말 판정이 아니다.

[![90초 시연 영상 · 무음 — YouTube에서 재생](https://img.youtube.com/vi/3o-WRERS_u8/hqdefault.jpg)](https://youtu.be/3o-WRERS_u8)

- 데모 설명·실행법: [demo/live-contradiction/README.md](demo/live-contradiction/README.md)
- 테스트: `uv run --extra dev pytest tests -q`
- UI 회귀: `node --test demo/live-contradiction/web/model.test.mjs`
- 제출 문구: [docs/SUBMISSION.md](docs/SUBMISSION.md)
- 검증 범위: [docs/DEMO_VERIFICATION.md](docs/DEMO_VERIFICATION.md)

이 저장소는 2026-11-09 해커톤에서 새로 만든 부분만 담는다. `korean_politics_news_engine/`은 팩트콕 비공개 엔진에서 데모가 쓰는 함수만 옮긴 것이다(`embeddings.py` 전체, `youtube_factcheck.py`의 Codex 실행 함수).
