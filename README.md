# 팩트콕 · 말 바꾸기 즉시 소환 (해커톤 데모)

정치인이 영상에서 말하는 순간, 같은 사람이 예전에 다르게 말한 기록을 날짜와 원문 링크와 함께 옆에 띄운다. 과거 발언은 gbrain에 쌓고, 지금 발언과의 관계는 Codex로 판정한다.

- 데모 설명·실행법: [demo/live-contradiction/README.md](demo/live-contradiction/README.md)
- 테스트: `uv run --extra dev pytest tests -q`

이 저장소는 2026-11-09 해커톤에서 새로 만든 부분만 담는다. `korean_politics_news_engine/`은 팩트콕 비공개 엔진에서 데모가 쓰는 함수만 옮긴 것이다(`embeddings.py` 전체, `youtube_factcheck.py`의 Codex 실행 함수).
