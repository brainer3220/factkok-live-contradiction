"""미디어 입력 검증과 재현 CLI의 파일 보존 계약을 확인한다."""

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "demo" / "live-contradiction"))

import build_media


def manifest_file(tmp_path, **changes):
    row = {
        "source_page": "https://imnews.imbc.com/news/example.html",
        "input": "source.mp4", "broadcaster": "MBC", "date": "2024-12-12",
        "source_start": 15, "source_end": 32, "slot_seconds": 55,
    }
    row.update(changes)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps([row]))
    return path


@pytest.mark.parametrize("changes", [
    {"input": "../outside.mp4"}, {"source_start": -1}, {"source_end": 15},
    {"slot_seconds": 16}, {"source_start": True}, {"source_end": float("inf")},
    {"audio_channel": "unknown"}, {"date": "tomorrow"},
])
def test_manifest_rejects_invalid_clip_boundaries(tmp_path, changes):
    with pytest.raises(build_media.MediaError):
        build_media.load_manifest(manifest_file(tmp_path, **changes))


def test_actual_manifests_define_supported_media():
    base = ROOT / "demo" / "live-contradiction"
    lee = build_media.load_manifest(base / "media-manifest.json")
    han = build_media.load_manifest(base / "profiles" / "han-dong-hoon.media.json")
    assert sum(clip.slot_seconds for clip in lee) == 90
    assert lee[2].audio_channel == "right"
    assert han[0].duration == 17
    assert han[0].slot_seconds == 55


def test_missing_ffmpeg_has_install_command(monkeypatch):
    monkeypatch.setattr(build_media.shutil, "which", lambda name: None)
    monkeypatch.setattr(build_media, "MAC_FFMPEG_PATHS", ())
    with pytest.raises(build_media.MediaError, match="brew install ffmpeg-full"):
        build_media.resolve_ffmpeg()


def test_missing_font_has_explicit_override(monkeypatch):
    monkeypatch.setattr(build_media, "FONT_PATHS", ())
    with pytest.raises(build_media.MediaError, match="--font"):
        build_media.resolve_font()


def setup_media(tmp_path, monkeypatch, **changes):
    manifest = manifest_file(tmp_path, **changes)
    (tmp_path / "source.mp4").write_bytes(b"fixture")
    font = tmp_path / "font.ttf"
    font.write_bytes(b"fixture")
    monkeypatch.setattr(build_media, "resolve_ffmpeg", lambda binary: "/tool/ffmpeg")
    monkeypatch.setattr(build_media, "resolve_font", lambda path: font)
    return manifest


def test_dry_run_does_not_encode_or_create_output(tmp_path, monkeypatch):
    manifest = setup_media(tmp_path, monkeypatch)
    monkeypatch.setattr(build_media, "_run", lambda *args, **kwargs: pytest.fail("encoded in dry run"))
    output = tmp_path / "output.mp4"
    build_media.build(manifest, tmp_path, output, dry_run=True)
    assert not output.exists()


def test_success_publishes_faststart_output_with_selected_audio(tmp_path, monkeypatch):
    manifest = setup_media(tmp_path, monkeypatch, audio_channel="right")
    commands = []

    def run(command, *, cwd):
        commands.append(command)
        (cwd / command[-1]).write_bytes(b"encoded-video")

    monkeypatch.setattr(build_media, "_run", run)
    output = tmp_path / "output.mp4"
    build_media.build(manifest, tmp_path, output)
    assert output.read_bytes() == b"encoded-video"
    encode = next(command for command in commands if "-vf" in command)
    assert encode[encode.index("-af") + 1] == "pan=mono|c0=c1,apad"
    assert "stop_duration=38" in encode[encode.index("-vf") + 1]
    assert "gte(t,17)" in encode[encode.index("-vf") + 1]
    assert "+faststart" in commands[-1]


def test_failed_encode_preserves_existing_output(tmp_path, monkeypatch):
    manifest = setup_media(tmp_path, monkeypatch)
    output = tmp_path / "output.mp4"
    output.write_bytes(b"existing-video")

    def fail(*args, **kwargs):
        raise build_media.MediaError("fixture failure")

    monkeypatch.setattr(build_media, "_run", fail)
    with pytest.raises(build_media.MediaError):
        build_media.build(manifest, tmp_path, output)
    assert output.read_bytes() == b"existing-video"
    assert not list(tmp_path.glob(".media-build-*"))


def test_media_cli_help_runs_from_another_directory(tmp_path):
    result = subprocess.run(
        [sys.executable, str(ROOT / "demo" / "live-contradiction" / "build_media.py"), "--help"],
        cwd=tmp_path, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0
    assert "--manifest" in result.stdout and "--font" in result.stdout
