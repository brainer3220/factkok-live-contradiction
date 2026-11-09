"""Codex provider의 프로필별 홈과 프로세스 환경 격리 계약."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "demo" / "live-contradiction"))

import pipeline as demo
import server
from korean_politics_news_engine import youtube_factcheck


def config(base_dir, model):
    return demo.DemoConfig("테스트 인물", "demo.mp4", "memory", "codex", 0.7, (), base_dir, judge_model=model)


def test_codex_judges_use_their_own_home_without_changing_process_environment(tmp_path, monkeypatch):
    original = tmp_path / "original"
    original.mkdir()
    monkeypatch.setenv("CODEX_HOME", str(original))
    homes = []
    for name in ("first", "second"):
        home = tmp_path / name
        home.mkdir()
        (home / "config.toml").write_text(f'model = "{name}"\n')
        (home / "auth.json").write_text(json.dumps({"fixture": name}))
        homes.append(home)
    observed = []

    def execute(command, *, input, check, capture_output, text, timeout, env):
        command_home = Path(env["CODEX_HOME"])
        observed.append((command[command.index("--model") + 1], (command_home / "config.toml").read_text(),
                         json.loads((command_home / "auth.json").read_text())["fixture"]))
        output = Path(command[command.index("--output-last-message") + 1])
        output.write_text('{"provider_response": "fixture"}')
        assert os.environ["CODEX_HOME"] == str(original)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(youtube_factcheck.subprocess, "run", execute)
    first = demo.CodexJudge(model="first", codex_home=homes[0])
    second = demo.CodexJudge(model="second", codex_home=homes[1])
    assert os.environ["CODEX_HOME"] == str(original)
    assert first.judge("synthetic prompt") == '{"provider_response": "fixture"}'
    assert second.judge("synthetic prompt") == '{"provider_response": "fixture"}'
    assert observed == [("first", 'model = "first"\n', "first"), ("second", 'model = "second"\n', "second")]
    assert os.environ["CODEX_HOME"] == str(original)


def test_profile_minimal_homes_copy_original_source_without_chaining(tmp_path, monkeypatch):
    original = tmp_path / "original"
    original.mkdir()
    (original / "auth.json").write_text('{"fixture": "original"}')
    (original / "config.toml").write_text('model = "original"\n[features]\nplugins = true\n')
    monkeypatch.setenv("CODEX_HOME", str(original))
    monkeypatch.setattr(server, "BASE_DIR", tmp_path)
    first = server.prepare_codex_home(config(tmp_path, "first"))
    second = server.prepare_codex_home(config(tmp_path / "profiles", "second"))
    assert first != second
    assert (first / "auth.json").read_bytes() == (second / "auth.json").read_bytes() == (original / "auth.json").read_bytes()
    assert 'model = "first"' in (first / "config.toml").read_text()
    assert 'model = "second"' in (second / "config.toml").read_text()
    assert "plugins = true" not in (first / "config.toml").read_text()
    assert os.environ["CODEX_HOME"] == str(original)
    assert first.joinpath("auth.json").stat().st_mode & 0o777 == 0o600


def test_adapter_without_home_argument_keeps_existing_environment_source(tmp_path, monkeypatch):
    original = tmp_path / "original"
    original.mkdir()
    (original / "config.toml").write_text('model = "original"\n')
    monkeypatch.setenv("CODEX_HOME", str(original))
    copied = youtube_factcheck.prepare_codex_home(tmp_path / "target")
    assert (copied / "config.toml").read_text() == 'model = "original"\n'
    assert os.environ["CODEX_HOME"] == str(original)
