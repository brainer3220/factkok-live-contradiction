"""실행기의 외부 명령 경계와 로컬 자료 준비를 검사한다."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.error

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "demo" / "live-contradiction"))

import build_media
import start_demo


def workspace(tmp_path, monkeypatch, *, videos=True):
    rows = []
    for name in ("lee", "han"):
        config = {
            "holder": name, "video": f"{name}.mp4", "store": "gbrain", "judge": "codex",
            "statements": [{
                "statement_id": name,
                "past": {"text": "과거", "date": "2024-01-01", "source_url": "https://example.com/past", "verified_at": "2026-11-09"},
                "current": {"text": "현재", "segment_index": 0},
            }],
        }
        (tmp_path / f"{name}.yaml").write_text(yaml.safe_dump(config))
        (tmp_path / f"{name}.transcript.json").write_text(json.dumps([
            {"index": 0, "start": 0, "end": 1, "speaker": name, "text": "현재"},
        ]))
        if videos:
            (tmp_path / f"{name}.mp4").write_bytes(b"\x00\x00\x00\x08moov")
        rows.append({"id": name, "name": name, "config": f"{name}.yaml", "transcript": f"{name}.transcript.json"})
    (tmp_path / "people.json").write_text(json.dumps({"people": rows}))
    monkeypatch.setattr(start_demo, "BASE_DIR", tmp_path)
    monkeypatch.delenv("LIVE_DEMO_GBRAIN_HOME", raising=False)
    monkeypatch.setattr(start_demo.shutil, "which", lambda name: f"/tools/{Path(name).name}")
    monkeypatch.setattr(start_demo.prepare, "url_responds", lambda url: True)
    return tmp_path


def brain_config(home):
    path = home / ".gbrain" / "config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "engine": "pglite", "embedding_disabled": True,
        "database_path": str(home / ".gbrain" / "brain.pglite"), "preserved": True,
    }))
    return path


def process_boundary(monkeypatch):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        if command[1:3] == ["init", "--help"]:
            return subprocess.CompletedProcess(command, 0, " ".join(start_demo.INIT_FLAGS), "")
        if command[1:2] == ["init"]:
            brain_config(Path(kwargs["env"]["GBRAIN_HOME"]))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(start_demo.subprocess, "run", run)
    return calls


def test_fresh_start_initializes_local_brain_then_seeds_both_profiles(tmp_path, monkeypatch):
    workspace(tmp_path, monkeypatch)
    calls = process_boundary(monkeypatch)
    monkeypatch.setenv("GBRAIN_DATABASE_URL", "fixture-remote-database")
    monkeypatch.setattr(sys, "argv", ["start_demo.py", "--port", "9876"])
    assert start_demo.main() == 0
    mutations = [(command, kwargs) for command, kwargs in calls if (
        "--pglite" in command or "seed" in command or command[1].endswith("server.py")
    )]
    assert "--pglite" in mutations[0][0]
    assert all(flag in mutations[0][0] for flag in start_demo.INIT_FLAGS)
    assert [Path(item[0][-1]).name for item in mutations[1:3]] == ["lee.yaml", "han.yaml"]
    assert mutations[-1][0][-2:] == ["--port", "9876"]
    for command, kwargs in mutations:
        assert kwargs["env"]["LIVE_DEMO_GBRAIN_HOME"] == str(tmp_path / ".gbrain")
        assert "GBRAIN_DATABASE_URL" not in kwargs["env"]
        assert "setup" not in command and "--force" not in command
    assert os.environ["GBRAIN_DATABASE_URL"] == "fixture-remote-database"


def test_existing_brain_is_preserved_and_media_is_not_downloaded(tmp_path, monkeypatch):
    workspace(tmp_path, monkeypatch)
    config = brain_config(tmp_path / ".gbrain")
    before = config.read_bytes()
    calls = process_boundary(monkeypatch)
    monkeypatch.setattr(start_demo, "source_hls", lambda url: pytest.fail("downloaded existing media"))
    monkeypatch.setattr(sys, "argv", ["start_demo.py", "--prepare-media"])
    assert start_demo.main() == 0
    assert config.read_bytes() == before
    assert not any("--pglite" in command for command, _ in calls)


def test_override_home_is_used_by_init_seed_and_server(tmp_path, monkeypatch):
    workspace(tmp_path, monkeypatch)
    override = tmp_path / "custom-brain"
    monkeypatch.setenv("LIVE_DEMO_GBRAIN_HOME", str(override))
    calls = process_boundary(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["start_demo.py"])
    assert start_demo.main() == 0
    assert all(kwargs["env"]["GBRAIN_HOME"] == str(override) for _, kwargs in calls)
    assert (override / ".gbrain" / "config.json").is_file()
    assert not (tmp_path / ".gbrain").exists()


def test_check_only_never_initializes_seeds_or_starts(tmp_path, monkeypatch):
    workspace(tmp_path, monkeypatch)
    calls = process_boundary(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["start_demo.py", "--check-only"])
    assert start_demo.main() == 0
    assert not any("--pglite" in command or "seed" in command for command, _ in calls)
    assert not any(command[1].endswith("server.py") for command, _ in calls)
    assert not (tmp_path / ".gbrain").exists()


def test_missing_media_fails_before_brain_changes(tmp_path, monkeypatch, capsys):
    workspace(tmp_path, monkeypatch, videos=False)
    calls = process_boundary(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["start_demo.py"])
    assert start_demo.main() == 1
    assert "--prepare-media" in capsys.readouterr().err
    assert not any("--pglite" in command or "seed" in command for command, _ in calls)


def test_missing_tools_give_install_commands_without_running_install(tmp_path, monkeypatch, capsys):
    workspace(tmp_path, monkeypatch)
    monkeypatch.setattr(start_demo.shutil, "which", lambda name: None)
    monkeypatch.setattr(start_demo.build_media, "MAC_FFMPEG_PATHS", ())
    calls = process_boundary(monkeypatch)
    monkeypatch.setattr(sys, "argv", ["start_demo.py"])
    assert start_demo.main() == 1
    error = capsys.readouterr().err
    assert "github:garrytan/gbrain#stable" in error
    assert "codex login" in error and "ffmpeg" in error
    assert calls == []


@pytest.mark.parametrize("markup", [
    '<script>{"contentUrl":"//video.imnews.imbc.com/a.mp4/playlist.m3u8"}</script>',
    'thisMovie1.file = "//video.imnews.imbc.com/a.mp4/playlist.m3u8";',
    '<source src="https://video.imnews.imbc.com/a.mp4/playlist.m3u8">',
])
def test_extracts_only_official_player_hls(markup):
    assert start_demo.extract_hls(markup) == "https://video.imnews.imbc.com/a.mp4/playlist.m3u8"


def test_channel_a_token_remains_in_memory_only():
    url = "https://channelavod.ichannela.com/a.mp4/playlist.m3u8?token=fixture-token"
    assert start_demo.extract_hls(json.dumps({"contentUrl": url})) == url
    with pytest.raises(start_demo.LaunchError):
        start_demo.extract_hls('<source src="https://untrusted.example/playlist.m3u8?token=fixture-token">')


def test_malformed_player_url_does_not_hide_valid_official_hls():
    markup = '<source src="https://[invalid/playlist.m3u8">' + (
        '<source src="https://video.imnews.imbc.com/a.mp4/playlist.m3u8">'
    )
    assert start_demo.extract_hls(markup) == "https://video.imnews.imbc.com/a.mp4/playlist.m3u8"


def test_source_request_failure_does_not_echo_sensitive_error(monkeypatch):
    def fail(*args, **kwargs):
        raise urllib.error.URLError("fixture-sensitive-detail")

    monkeypatch.setattr(start_demo.urllib.request, "urlopen", fail)
    with pytest.raises(start_demo.LaunchError) as caught:
        start_demo.source_hls("https://imnews.imbc.com/news/example.html")
    assert "fixture-sensitive-detail" not in str(caught.value)
    assert "URLError" in str(caught.value)


def test_unsupported_source_is_rejected_before_request(monkeypatch):
    monkeypatch.setattr(start_demo.urllib.request, "urlopen", lambda *args, **kwargs: pytest.fail("requested unsupported source"))
    with pytest.raises(start_demo.LaunchError):
        start_demo.source_hls("https://untrusted.example/article.html")


def test_existing_media_needs_no_network_or_build(tmp_path, monkeypatch):
    video = tmp_path / "existing.mp4"
    video.write_bytes(b"fixture")
    assets = start_demo.ProfileAssets("한동훈", tmp_path / "config.yaml", tmp_path / "transcript.json", tmp_path / "missing-manifest.json", video)
    monkeypatch.setattr(start_demo, "source_hls", lambda url: pytest.fail("downloaded existing media"))
    start_demo.prepare_media(assets, tmp_path / "cache", {}, {}, font=None, ffmpeg="missing")
    assert video.read_bytes() == b"fixture"


def test_failed_download_leaves_no_partial_or_token_file(tmp_path, monkeypatch, capsys):
    manifest = tmp_path / "media.json"
    manifest.write_text(json.dumps([{
        "source_page": "https://ichannela.com/article.html", "input": "source.mp4",
        "broadcaster": "Channel A", "date": "2025-05-28",
        "source_start": 1, "source_end": 2, "slot_seconds": 10,
    }]))
    assets = start_demo.ProfileAssets("인물", tmp_path / "config.yaml", tmp_path / "transcript.json", manifest, tmp_path / "output.mp4")
    monkeypatch.setattr(build_media, "resolve_ffmpeg", lambda binary: "ffmpeg")
    monkeypatch.setattr(build_media, "resolve_font", lambda font: tmp_path / "font.ttf")
    monkeypatch.setattr(start_demo, "source_hls", lambda page: "https://channelavod.ichannela.com/a/playlist.m3u8?token=fixture-token")

    def fail(*args, **kwargs):
        raise start_demo.LaunchError("다운로드 실패")

    monkeypatch.setattr(start_demo, "run_quiet", fail)
    cache = tmp_path / "cache"
    with pytest.raises(start_demo.LaunchError):
        start_demo.prepare_media(assets, cache, {"ffmpeg": "ffmpeg"}, {}, font=None, ffmpeg="ffmpeg")
    assert list(cache.iterdir()) == []
    assert not assets.video.exists()
    assert "fixture-token" not in capsys.readouterr().out


def test_launcher_help_has_no_pythonpath_dependency(tmp_path):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    result = subprocess.run(
        [sys.executable, str(ROOT / "demo" / "live-contradiction" / "start_demo.py"), "--help"],
        cwd=tmp_path, env=env, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--prepare-media" in result.stdout and "--check-only" in result.stdout


def media_preparation(tmp_path, monkeypatch, *, duration=10):
    manifest = tmp_path / "original-media.json"
    manifest.write_text(json.dumps([{
        "source_page": "https://imnews.imbc.com/news/official.html",
        "input": "source.mp4", "broadcaster": "MBC", "date": "2024-11-04",
        "source_start": 364, "source_end": 374, "slot_seconds": 30,
        "audio_channel": "right",
    }]))
    assets = start_demo.ProfileAssets(
        "인물", tmp_path / "config.yaml", tmp_path / "transcript.json",
        manifest, tmp_path / "output.mp4",
    )
    commands, requests, manifests = [], [], []
    monkeypatch.setattr(build_media, "resolve_ffmpeg", lambda binary: "ffmpeg")
    monkeypatch.setattr(build_media, "resolve_font", lambda font: tmp_path / "font.ttf")

    def source(page):
        requests.append(page)
        return "https://video.imnews.imbc.com/a.mp4/playlist.m3u8?token=fixture-token"

    def run(command, **kwargs):
        commands.append(command)
        if command[0] == "ffprobe":
            value = 535 if Path(command[-1]).name == "source.mp4" else duration
            return subprocess.CompletedProcess(command, 0, str(value), "")
        Path(command[-1]).write_bytes(b"trimmed-video")
        return subprocess.CompletedProcess(command, 0, "", "")

    def build(manifest_path, media_dir, output, **kwargs):
        normalized = json.loads(manifest_path.read_text())
        manifests.append(normalized)
        assert "fixture-token" not in manifest_path.read_text()
        assert (media_dir / normalized[0]["input"]).is_file()
        output.write_bytes(b"final-video")

    monkeypatch.setattr(start_demo, "source_hls", source)
    monkeypatch.setattr(start_demo, "run_quiet", run)
    monkeypatch.setattr(build_media, "build", build)
    return assets, commands, requests, manifests


def test_download_seeks_only_required_clip_and_preserves_original_manifest(tmp_path, monkeypatch, capsys):
    assets, commands, requests, manifests = media_preparation(tmp_path, monkeypatch)
    original = assets.manifest.read_bytes()
    cache = tmp_path / "cache"
    start_demo.prepare_media(
        assets, cache, {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"}, {},
        font=None, ffmpeg="ffmpeg",
    )
    download = next(command for command in commands if command[0] == "ffmpeg")
    assert download.index("-ss") < download.index("-i")
    seek_indices = [index for index, value in enumerate(download) if value == "-ss"]
    seek_values = [float(download[index + 1]) for index in seek_indices]
    assert seek_indices[0] < download.index("-i") < seek_indices[1]
    assert seek_values == [354, 10]
    assert sum(seek_values) == 364
    assert download[download.index("-t") + 1] == "10"
    assert download[download.index("-c:v") + 1] == "libx264"
    assert requests == ["https://imnews.imbc.com/news/official.html"]
    assert manifests[0][0]["source_start"] == 0
    assert manifests[0][0]["source_end"] == 10
    assert manifests[0][0]["audio_channel"] == "right"
    assert manifests[0][0]["source_page"] == requests[0]
    assert manifests[0][0]["input"] == "source-364.0-374.0.mp4"
    assert assets.manifest.read_bytes() == original
    assert assets.video.read_bytes() == b"final-video"
    assert [path.name for path in cache.iterdir()] == ["source-364.0-374.0.mp4"]
    assert "fixture-token" not in capsys.readouterr().out


def test_full_cache_is_cut_locally_without_network(tmp_path, monkeypatch):
    assets, commands, requests, manifests = media_preparation(tmp_path, monkeypatch)
    cache = tmp_path / "cache"
    cache.mkdir()
    full = cache / "source.mp4"
    full.write_bytes(b"original-full-video")
    start_demo.prepare_media(
        assets, cache, {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"}, {},
        font=None, ffmpeg="ffmpeg",
    )
    assert requests == []
    cut = next(command for command in commands if command[0] == "ffmpeg")
    assert cut[cut.index("-i") + 1] == str(full)
    assert full.read_bytes() == b"original-full-video"
    assert manifests[0][0]["source_start"] == 0


@pytest.mark.parametrize("duration, accepted", [(10, True), (9.95, True), (9.94, False)])
def test_clip_cache_uses_requested_duration_with_tolerance(tmp_path, monkeypatch, duration, accepted):
    assets, commands, requests, manifests = media_preparation(tmp_path, monkeypatch, duration=duration)
    cache = tmp_path / "cache"
    cache.mkdir()
    cached = cache / "source-364.0-374.0.mp4"
    cached.write_bytes(b"cached-clip")
    if accepted:
        start_demo.prepare_media(
            assets, cache, {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"}, {},
            font=None, ffmpeg="ffmpeg",
        )
        assert assets.video.is_file()
    else:
        with pytest.raises(start_demo.LaunchError, match="짧습니다") as caught:
            start_demo.prepare_media(
                assets, cache, {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"}, {},
                font=None, ffmpeg="ffmpeg",
            )
        assert f"실제 {duration:.3f}초" in str(caught.value)
        assert "필요 10.000초" in str(caught.value)
        assert "허용 오차 0.050초" in str(caught.value)
        assert not assets.video.exists()
        assert manifests == []
    assert requests == []
    assert not any(command[0] == "ffmpeg" for command in commands)
    assert cached.read_bytes() == b"cached-clip"


@pytest.mark.parametrize("start, expected_input, expected_output", [
    (0, 0, 0), (4.5, 0, 4.5), (10, 0, 10),
    (15, 5, 10), (53.16, 43.16, 10), (364, 354, 10),
])
def test_two_stage_seek_preserves_source_offset_and_requested_duration(
    tmp_path, monkeypatch, start, expected_input, expected_output,
):
    assets, commands, requests, manifests = media_preparation(tmp_path, monkeypatch)
    data = json.loads(assets.manifest.read_text())
    data[0]["source_start"] = start
    data[0]["source_end"] = start + 10
    assets.manifest.write_text(json.dumps(data))
    start_demo.prepare_media(
        assets, tmp_path / "cache", {"ffmpeg": "ffmpeg", "ffprobe": "ffprobe"}, {},
        font=None, ffmpeg="ffmpeg",
    )
    command = next(command for command in commands if command[0] == "ffmpeg")
    positions = [index for index, value in enumerate(command) if value == "-ss"]
    seeks = [float(command[index + 1]) for index in positions]
    assert positions[0] < command.index("-i") < positions[1]
    assert seeks == pytest.approx([expected_input, expected_output])
    assert sum(seeks) == pytest.approx(start)
    assert command[command.index("-t") + 1] == "10"
    assert manifests[0][0]["source_start"] == 0
    assert manifests[0][0]["source_end"] == 10
