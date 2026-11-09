"""전제 조건과 자료를 확인하고 작업 폴더의 기억 저장소와 데모 서버를 준비한다."""

from __future__ import annotations

import argparse
import dataclasses
import html
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR.parents[1]))

import build_media
import catalog
import pipeline as demo
import prepare

PAGE_HOSTS = frozenset(("imnews.imbc.com", "ichannela.com", "www.ichannela.com"))
MEDIA_HOSTS = frozenset(("video.imnews.imbc.com", "channelavod.ichannela.com"))
MAX_PAGE_BYTES = 2 * 1024 * 1024
SEEK_PREROLL_SECONDS = 10
INIT_FLAGS = ("--pglite", "--non-interactive", "--no-embedding", "--db-only", "--no-git")


class LaunchError(ValueError):
    """사용자가 해결할 수 있는 데모 전제 조건이 충족되지 않았다."""


@dataclasses.dataclass(frozen=True)
class ProfileAssets:
    name: str
    config: Path
    transcript: Path
    manifest: Path
    video: Path


def load_profiles(base_dir: Path) -> tuple[ProfileAssets, ...]:
    """카탈로그의 준비된 설정을 읽되 아직 없는 출력 영상은 허용한다."""
    data = json.loads((base_dir / "people.json").read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("people"), list):
        raise LaunchError("people.json의 people 목록이 잘못됐습니다.")
    profiles = []
    for row in data["people"]:
        if not isinstance(row, dict):
            raise LaunchError("people.json의 인물 정보가 잘못됐습니다.")
        if not row.get("config") and not row.get("transcript"):
            continue
        if not isinstance(row.get("config"), str) or not isinstance(row.get("transcript"), str):
            raise LaunchError("등록 인물에는 config와 transcript가 함께 필요합니다.")
        config_path = catalog.contained_file(base_dir, row["config"])
        transcript_path = catalog.contained_file(base_dir, row["transcript"])
        config = demo.load_config(config_path)
        if config.holder != row.get("name"):
            raise LaunchError("인물 이름과 설정의 holder가 다릅니다.")
        video = (config.base_dir / config.video).resolve()
        if Path(config.video).is_absolute() or not video.is_relative_to(base_dir.resolve()):
            raise LaunchError("영상은 데모 폴더 안의 상대 경로여야 합니다.")
        manifest = config_path.with_suffix(".media.json")
        if config_path.name == "demo.yaml":
            manifest = config_path.parent / "media-manifest.json"
        profiles.append(ProfileAssets(
            config.holder, config_path, transcript_path, manifest, video,
        ))
    if not profiles:
        raise LaunchError("등록된 실행 가능 인물이 없습니다.")
    return tuple(profiles)


def run_quiet(
    command: list[str],
    *,
    env: dict[str, str],
    cwd: Path,
    label: str,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    """외부 명령 출력에는 인증 정보가 있을 수 있어 실패 시 요약만 노출한다."""
    try:
        result = subprocess.run(
            command, env=env, cwd=cwd, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise LaunchError(f"{label} 실패: {type(error).__name__}") from None
    if result.returncode:
        raise LaunchError(f"{label} 실패(exit {result.returncode}).")
    return result


def prerequisites(env: dict[str, str], base_dir: Path, ffmpeg: str) -> dict[str, str]:
    names = {
        "gbrain": ("gbrain", "bun install -g github:garrytan/gbrain#stable"),
        "codex": (os.getenv("LIVE_DEMO_CODEX_BIN", "codex"), "npm install -g @openai/codex && codex login"),
        "ffmpeg": (ffmpeg, "macOS: brew install ffmpeg-full / Debian·Ubuntu: sudo apt-get install ffmpeg"),
        "ffprobe": ("ffprobe", "macOS: brew install ffmpeg-full / Debian·Ubuntu: sudo apt-get install ffmpeg"),
    }
    binaries = {}
    missing = []
    for name, (binary, hint) in names.items():
        found = shutil.which(binary)
        if not found and name in ("ffmpeg", "ffprobe") and ffmpeg == "ffmpeg":
            found = next((
                resolved for path in build_media.MAC_FFMPEG_PATHS
                if (resolved := shutil.which(str(path.with_name(name))))
            ), None)
        if found:
            binaries[name] = found
        else:
            missing.append(f"{name} 실행 파일이 없습니다. 해결 명령: {hint}")
    if missing:
        raise LaunchError("\n".join(missing))
    try:
        result = run_quiet(
            [binaries["gbrain"], "init", "--help"], env=env, cwd=base_dir,
            label="gbrain 설치 확인",
        )
    except LaunchError:
        raise LaunchError(
            "gbrain을 실행할 수 없습니다. Bun 1.4.4 이상을 준비한 뒤 "
            "bun install -g github:garrytan/gbrain#stable로 공식 CLI를 설치하세요."
        ) from None
    if not all(flag in result.stdout for flag in INIT_FLAGS):
        raise LaunchError(
            "설치된 gbrain이 필요한 초기화 옵션을 지원하지 않습니다. "
            "bun install -g github:garrytan/gbrain#stable로 공식 CLI를 설치하세요."
        )
    try:
        run_quiet(
            [binaries["codex"], "login", "status"], env=env, cwd=base_dir,
            label="Codex 로그인 확인",
        )
    except LaunchError:
        raise LaunchError("Codex 로그인 상태를 확인할 수 없습니다. codex login을 실행하세요.") from None
    return binaries


def extract_hls(page_html: str) -> str:
    """공식 기사 플레이어의 HLS만 선택하고 토큰은 메모리에만 둔다."""
    candidates = []
    for match in re.finditer(r'"contentUrl"\s*:\s*("(?:\\.|[^"\\])*")', page_html):
        try:
            candidates.append(json.loads(match.group(1)))
        except json.JSONDecodeError:
            continue
    for pattern in (
        r"thisMovie\d*\.file\s*=\s*(['\"])(.*?)\1",
        r"<source\b[^>]*\bsrc\s*=\s*(['\"])(.*?)\1",
    ):
        candidates.extend(match.group(2) for match in re.finditer(
            pattern, page_html, flags=re.IGNORECASE | re.DOTALL,
        ))
    for candidate in candidates:
        if not isinstance(candidate, str):
            continue
        try:
            url = urllib.parse.urljoin("https://", html.unescape(candidate))
            parsed = urllib.parse.urlsplit(url)
        except ValueError:
            continue
        if (
            parsed.scheme == "https" and parsed.hostname in MEDIA_HOSTS
            and not parsed.username and not parsed.password
            and parsed.path.endswith("/playlist.m3u8") and len(url) <= 8192
        ):
            return url
    raise LaunchError("공식 기사 HTML에서 허용된 HLS 영상 주소를 찾지 못했습니다.")


def source_hls(page_url: str) -> str:
    try:
        parsed = urllib.parse.urlsplit(page_url)
    except ValueError:
        raise LaunchError("미디어 출처 URL의 형식이 잘못됐습니다.") from None
    if (
        parsed.scheme != "https" or parsed.hostname not in PAGE_HOSTS
        or parsed.username or parsed.password
    ):
        raise LaunchError("미디어 출처는 등록된 공식 MBC·채널A 기사여야 합니다.")
    request = urllib.request.Request(page_url, headers={"User-Agent": "factkok-local-demo/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            redirected = urllib.parse.urlsplit(response.geturl())
            if redirected.scheme != "https" or redirected.hostname not in PAGE_HOSTS:
                raise LaunchError("공식 기사 요청이 허용되지 않은 사이트로 이동했습니다.")
            body = response.read(MAX_PAGE_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise LaunchError(f"공식 기사 HTML 요청 실패: {type(error).__name__}") from None
    if len(body) > MAX_PAGE_BYTES:
        raise LaunchError("공식 기사 HTML의 크기가 허용 범위를 넘었습니다.")
    return extract_hls(body.decode("utf-8", errors="replace"))


def verify_source(
    path: Path,
    clip: build_media.MediaClip,
    binaries: dict[str, str],
    env: dict[str, str],
    *,
    minimum_duration: float | None = None,
) -> None:
    result = run_quiet(
        [binaries["ffprobe"], "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        env=env, cwd=path.parent, label=f"원본 검사({clip.input})",
    )
    try:
        duration = float(result.stdout.strip())
    except ValueError:
        raise LaunchError(f"원본 영상 길이를 확인할 수 없습니다: {clip.input}") from None
    required_duration = clip.source_end if minimum_duration is None else minimum_duration
    if not math.isfinite(duration) or duration + 0.05 < required_duration:
        raise LaunchError(
            f"원본 영상이 필요한 발언 구간보다 짧습니다: {clip.input} "
            f"(실제 {duration:.3f}초, 필요 {required_duration:.3f}초, 허용 오차 0.050초)"
        )


def prepare_media(
    profile: ProfileAssets,
    cache_dir: Path,
    binaries: dict[str, str],
    env: dict[str, str],
    *,
    font: Path | None,
    ffmpeg: str,
) -> None:
    if profile.video.is_file():
        return
    clips = build_media.load_manifest(profile.manifest)
    render_binary = build_media.resolve_ffmpeg(ffmpeg)
    selected_font = build_media.resolve_font(font)
    cache_dir.mkdir(parents=True, exist_ok=True)
    prepared_clips = []
    for clip in clips:
        filename = f"{Path(clip.input).stem}-{clip.source_start}-{clip.source_end}.mp4"
        target = cache_dir / filename
        prepared_clips.append(dataclasses.replace(
            clip, input=filename, source_start=0, source_end=clip.duration,
        ))
        if target.is_file():
            verify_source(target, clip, binaries, env, minimum_duration=clip.duration)
            continue
        full_source = cache_dir / clip.input
        if full_source.is_file():
            verify_source(full_source, clip, binaries, env)
            source = str(full_source)
        else:
            source = source_hls(clip.source_page)
        with tempfile.NamedTemporaryFile(suffix=".mp4", dir=cache_dir, delete=False) as handle:
            temporary = Path(handle.name)
        input_seek = max(0, clip.source_start - SEEK_PREROLL_SECONDS)
        output_seek = clip.source_start - input_seek
        try:
            run_quiet(
                [binaries["ffmpeg"], "-hide_banner", "-loglevel", "error", "-y",
                 "-ss", f"{input_seek:g}", "-i", source,
                 "-ss", f"{output_seek:g}", "-t", f"{clip.duration:g}",
                 "-map", "0:v:0", "-map", "0:a:0", "-c:v", "libx264",
                 "-preset", "fast", "-crf", "18", "-c:a", "aac",
                 "-movflags", "+faststart", str(temporary)],
                env=env, cwd=cache_dir, label=f"발언 구간 준비({clip.input})", timeout=300,
            )
            verify_source(temporary, clip, binaries, env, minimum_duration=clip.duration)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="build-manifest-", dir=cache_dir) as directory:
        manifest = Path(directory) / "media.json"
        manifest.write_text(
            json.dumps([dataclasses.asdict(clip) for clip in prepared_clips], ensure_ascii=False),
            encoding="utf-8",
        )
        build_media.build(
            manifest, cache_dir, profile.video,
            ffmpeg=render_binary, font=selected_font,
        )


def prepare_brain(home: Path, binary: str, env: dict[str, str], base_dir: Path) -> None:
    config_path = home / ".gbrain" / "config.json"
    if not config_path.is_file():
        if (home / "config.json").exists():
            raise LaunchError("기존 gbrain 설정의 위치가 설치된 CLI 규칙과 다릅니다. 기존 설정을 확인하세요.")
        run_quiet(
            [binary, "init", *INIT_FLAGS], env=env, cwd=base_dir,
            label="로컬 gbrain 초기화", timeout=120,
        )
    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise LaunchError("작업 폴더에서 gbrain config.json을 읽을 수 없습니다.") from None
    database_path = config.get("database_path") if isinstance(config, dict) else None
    if (
        not isinstance(config, dict) or config.get("engine") != "pglite"
        or config.get("embedding_disabled") is not True
        or not isinstance(database_path, str)
        or not Path(database_path).resolve().is_relative_to(home.resolve())
    ):
        raise LaunchError("기존 gbrain 설정이 작업 폴더의 no-embedding PGLite가 아닙니다. 설정을 덮어쓰지 않았습니다.")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-media", action="store_true", help="없는 영상만 공식 기사에서 준비한다")
    parser.add_argument("--check-only", action="store_true", help="설치·자료만 점검하고 저장소를 변경하거나 서버를 실행하지 않는다")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--font", type=Path)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("--port는 1~65535여야 합니다.")
    home = Path(os.getenv("LIVE_DEMO_GBRAIN_HOME", str(BASE_DIR / ".gbrain"))).expanduser().resolve()
    env = {**os.environ, "LIVE_DEMO_GBRAIN_HOME": str(home), "GBRAIN_HOME": str(home)}
    env.pop("GBRAIN_DATABASE_URL", None)
    env.pop("DATABASE_URL", None)
    try:
        profiles = load_profiles(BASE_DIR)
        binaries = prerequisites(env, BASE_DIR, args.ffmpeg)
        missing = [profile for profile in profiles if not profile.video.is_file()]
        if missing and (not args.prepare_media or args.check_only):
            names = ", ".join(profile.name for profile in missing)
            raise LaunchError(
                f"영상이 없습니다({names}). "
                "uv run python demo/live-contradiction/start_demo.py --prepare-media를 실행하세요."
            )
        for profile in missing:
            print(f"공식 영상 준비: {profile.name}", flush=True)
            prepare_media(
                profile, BASE_DIR / ".media-cache", binaries, env,
                font=args.font, ffmpeg=args.ffmpeg,
            )
        for profile in profiles:
            problems = prepare.check(profile.config, profile.transcript)
            if problems:
                raise LaunchError(f"{profile.name} 자료 검사 실패\n" + "\n".join(problems))
        print(f"전제 조건·자료 검사 통과: {len(profiles)}명", flush=True)
        if args.check_only:
            return 0
        prepare_brain(home, binaries["gbrain"], env, BASE_DIR)
        for profile in profiles:
            run_quiet(
                [sys.executable, str(BASE_DIR / "prepare.py"), "seed", "--config", str(profile.config)],
                env=env, cwd=BASE_DIR, label=f"과거 발언 준비({profile.name})", timeout=120,
            )
        print(f"로컬 기억 저장소 준비 완료. http://127.0.0.1:{args.port}", flush=True)
        env["LIVE_DEMO_CODEX_BIN"] = binaries["codex"]
        return subprocess.run(
            [sys.executable, str(BASE_DIR / "server.py"), "--port", str(args.port)],
            env=env, cwd=BASE_DIR, check=False,
        ).returncode
    except (LaunchError, build_media.MediaError, demo.ConfigError, OSError, json.JSONDecodeError) as error:
        print(f"데모 준비 실패: {error}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
