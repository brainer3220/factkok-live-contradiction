# Factkok · Statement Comparison Demo

**English** | [한국어](README.ko.md)

Choose a public figure and play a video to compare their statements with earlier remarks from the same person, with dates and links to original sources. gbrain retrieves the records, and an OpenAI model via Codex assesses how the statements relate. A change in position is not a finding that someone lied.

[![Watch the demo on YouTube](https://img.youtube.com/vi/e7cd_2SQ6Ks/hqdefault.jpg)](https://youtu.be/e7cd_2SQ6Ks)

[Previous demo · 90 seconds, silent](https://youtu.be/3o-WRERS_u8)

## Run locally

Run from a Git checkout with Python 3.11+, uv, gbrain, a logged-in Codex CLI, and FFmpeg/ffprobe. Preparing the videos also requires FFmpeg's `drawtext` filter and a Korean font.

```bash
uv sync --extra dev
uv run python demo/live-contradiction/start_demo.py --prepare-media
```

Open [localhost:8765](http://127.0.0.1:8765). Omit `--prepare-media` on subsequent runs once the videos are ready. The demo uses recorded video and aligned transcripts; it does not perform live speech recognition.

## Resources

- [Detailed setup and demo guide](demo/live-contradiction/README.md) (Korean)
- Python tests: `uv run --extra dev pytest tests -q`
- UI state tests: `node --test demo/live-contradiction/web/model.test.mjs`
- [Submission draft](docs/SUBMISSION.md) (Korean)
- [Verification scope and results](docs/DEMO_VERIFICATION.md) (Korean)

This repository contains the work created for the November 9, 2026 hackathon. `korean_politics_news_engine/` contains only the parts of Factkok's private engine used by this demo: `embeddings.py` and the Codex execution helper from `youtube_factcheck.py`.
