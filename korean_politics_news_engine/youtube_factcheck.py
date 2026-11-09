"""팩트콕 비공개 엔진 korean_politics_news_engine/youtube_factcheck.py에서 데모가 쓰는
Codex 실행 함수만 옮긴 부분집합. 원본의 broker 분기는 제외했다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def summarize_process_error(value: str) -> str:
    cleaned = [line.strip() for line in value.splitlines() if line.strip()]
    return " ".join(cleaned[-4:])[:800] if cleaned else "no process output"


def prepare_codex_home(parent_dir: Path, *, source_home: Path | None = None) -> Path:
    codex_home = parent_dir / "codex-home"
    codex_home.mkdir(parents=True, exist_ok=True)
    if source_home is None:
        source_home = Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex"))).expanduser()
    for filename in ("auth.json", "config.toml", "installation_id"):
        source = source_home / filename
        if source.exists():
            shutil.copy2(source, codex_home / filename)
    return codex_home


def run_codex_json_prompt(
    codex: str,
    prompt: str,
    schema: dict,
    *,
    prefix: str,
    output_filename: str,
    codex_model: str = "",
    timeout: int = 180,
    search: bool = False,
    codex_home: Path | None = None,
) -> str:
    with tempfile.TemporaryDirectory(prefix=prefix) as temp_dir:
        temp_path = Path(temp_dir)
        schema_path = temp_path / "schema.json"
        output_path = temp_path / output_filename
        schema_path.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")
        command_home = prepare_codex_home(temp_path, source_home=codex_home)
        cmd = [codex]
        if search:
            cmd.append("--search")
        cmd.extend(
            [
                "exec",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "--cd",
                os.getenv("KPN_PROJECT_ROOT", str(Path.cwd())),
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
            ]
        )
        model = codex_model or os.getenv("KPN_YOUTUBE_CLAIM_CODEX_MODEL", "")
        if model:
            cmd.extend(["--model", model])
        cmd.append("-")
        env = {**os.environ, "CODEX_HOME": str(command_home)}
        completed = subprocess.run(cmd, input=prompt, check=False, capture_output=True, text=True, timeout=timeout, env=env)
        if completed.returncode != 0:
            detail = summarize_process_error(completed.stderr or completed.stdout)
            raise RuntimeError(f"codex JSON prompt failed: {detail}")
        return output_path.read_text(encoding="utf-8") if output_path.exists() else completed.stdout
