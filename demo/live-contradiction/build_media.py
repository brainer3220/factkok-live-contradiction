"""출처와 편집 안내를 표시한 로컬 리허설 영상을 manifest에서 만든다."""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import json
import math
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

BASE_DIR = Path(__file__).resolve().parent
MAC_FFMPEG_PATHS = (
    Path("/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"),
    Path("/usr/local/opt/ffmpeg-full/bin/ffmpeg"),
)
FONT_PATHS = (
    Path("/System/Library/Fonts/AppleSDGothicNeo.ttc"),
    Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    Path("/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf"),
)


class MediaError(ValueError):
    """미디어 입력이나 설치 상태로 인해 영상을 만들 수 없다."""


@dataclasses.dataclass(frozen=True)
class MediaClip:
    source_page: str
    input: str
    broadcaster: str
    date: str
    source_start: float
    source_end: float
    slot_seconds: float
    audio_channel: str = "stereo"

    @property
    def duration(self) -> float:
        return self.source_end - self.source_start


def load_manifest(path: Path) -> tuple[MediaClip, ...]:
    """외부 JSON을 검증해 편집에 필요한 필드만 읽는다."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise MediaError(f"미디어 manifest를 읽을 수 없습니다: {path.name}") from None
    if not isinstance(data, list) or not data:
        raise MediaError("미디어 manifest에는 한 개 이상의 구간이 필요합니다.")
    clips = []
    for index, row in enumerate(data):
        if not isinstance(row, dict):
            raise MediaError(f"미디어 구간 {index}는 객체여야 합니다.")
        for key in ("source_page", "input", "broadcaster", "date"):
            if not isinstance(row.get(key), str) or not row[key].strip():
                raise MediaError(f"미디어 구간 {index}의 {key}가 비어 있습니다.")
        filename = Path(row["input"])
        if filename.name != row["input"] or filename.suffix != ".mp4":
            raise MediaError(f"미디어 구간 {index}의 input은 MP4 파일명이어야 합니다.")
        try:
            datetime.date.fromisoformat(row["date"])
        except ValueError:
            raise MediaError(f"미디어 구간 {index}의 날짜가 잘못됐습니다.") from None
        for key in ("source_start", "source_end", "slot_seconds"):
            value = row.get(key)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise MediaError(f"미디어 구간 {index}의 {key}는 유한한 수여야 합니다.")
        start, end, slot = (float(row[key]) for key in (
            "source_start", "source_end", "slot_seconds"
        ))
        if start < 0 or not 0 < end - start <= slot:
            raise MediaError(f"미디어 구간 {index}의 발언 길이가 재생 슬롯 범위 밖입니다.")
        channel = row.get("audio_channel", "stereo")
        if channel not in ("stereo", "left", "right"):
            raise MediaError(f"미디어 구간 {index}의 오디오 채널이 잘못됐습니다.")
        clips.append(MediaClip(
            row["source_page"], row["input"], row["broadcaster"], row["date"],
            start, end, slot, channel,
        ))
    return tuple(clips)


def _run(command: list[str], *, cwd: Path | None = None) -> None:
    try:
        result = subprocess.run(
            command, cwd=cwd, capture_output=True, text=True, timeout=300,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise MediaError(f"FFmpeg 실행 실패: {type(error).__name__}") from None
    if result.returncode:
        raise MediaError(f"FFmpeg 영상 생성 실패(exit {result.returncode}).")


def resolve_ffmpeg(binary: str = "ffmpeg") -> str:
    """drawtext 지원 실행 파일을 찾고 없으면 설치 방법을 알린다."""
    selected = shutil.which(binary)
    candidates = [selected] if selected else []
    if binary == "ffmpeg":
        candidates.extend(str(path) for path in MAC_FFMPEG_PATHS if path.is_file())
    for candidate in candidates:
        try:
            result = subprocess.run(
                [candidate, "-hide_banner", "-filters"], capture_output=True,
                text=True, timeout=10, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if result.returncode == 0 and "drawtext" in result.stdout:
            return candidate
    raise MediaError(
        "drawtext가 있는 FFmpeg가 필요합니다. macOS는 brew install ffmpeg-full, "
        "Debian/Ubuntu는 sudo apt-get install ffmpeg를 실행한 뒤 "
        "--ffmpeg로 해당 실행 파일을 지정하세요."
    )


def resolve_font(path: Path | None = None) -> Path:
    """명시한 한글 폰트를 우선하고 알려진 설치 경로를 확인한다."""
    if path is not None:
        font = path.expanduser().resolve()
        if not font.is_file():
            raise MediaError("--font로 지정한 폰트 파일이 없습니다.")
        return font
    for font in FONT_PATHS:
        if font.is_file():
            return font
    raise MediaError(
        "한글 폰트가 없습니다. --font /설치된/한글폰트.ttf를 지정하세요. "
        "Debian/Ubuntu는 sudo apt-get install fonts-noto-cjk로 설치할 수 있습니다."
    )


def _filter_path(path: Path) -> str:
    value = str(path)
    for character in ("\\", "'", ":", ",", "[", "]"):
        value = value.replace(character, "\\" + character)
    return value


def build(
    manifest: Path,
    media_dir: Path,
    output: Path,
    *,
    ffmpeg: str = "ffmpeg",
    font: Path | None = None,
    dry_run: bool = False,
) -> None:
    clips = load_manifest(manifest)
    binary = resolve_ffmpeg(ffmpeg)
    selected_font = resolve_font(font)
    for clip in clips:
        if not (media_dir / clip.input).is_file():
            raise MediaError(f"원본 영상이 없습니다: {clip.input}")
    if dry_run:
        print(f"미디어 준비 통과: {len(clips)}구간, {sum(c.slot_seconds for c in clips):g}초")
        return
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".media-build-", dir=output.parent) as directory:
        temp = Path(directory)
        for index, clip in enumerate(clips):
            label = temp / f"label-{index}.txt"
            label.write_text(
                f"자료 영상 · {clip.broadcaster} · {clip.date} · 발언 뒤 정지 화면·무음",
                encoding="utf-8",
            )
            hold = temp / "hold.txt"
            hold.write_text("실제 발언 종료 · 판정 대기 · 정지 화면과 무음", encoding="utf-8")
            filters = (
                "scale=1194:672,pad=1280:720:43:48:black,setsar=1,fps=30,"
                f"tpad=stop_mode=clone:stop_duration={clip.slot_seconds-clip.duration:g},"
                f"drawtext=fontfile={_filter_path(selected_font)}:"
                f"textfile={label.name}:fontcolor=white:fontsize=23:x=18:y=12,"
                f"drawtext=fontfile={_filter_path(selected_font)}:"
                f"textfile={hold.name}:fontcolor=white:fontsize=27:box=1:"
                "boxcolor=black@0.8:boxborderw=12:x=(w-tw)/2:y=85:"
                f"enable='gte(t,{clip.duration:g})'"
            )
            audio = "apad"
            if clip.audio_channel in ("left", "right"):
                channel = 1 if clip.audio_channel == "right" else 0
                audio = f"pan=mono|c0=c{channel},apad"
            _run([
                binary, "-hide_banner", "-loglevel", "error", "-y",
                "-ss", f"{clip.source_start:g}", "-t", f"{clip.duration:g}",
                "-i", str((media_dir / clip.input).resolve()),
                "-vf", filters, "-af", audio, "-t", f"{clip.slot_seconds:g}",
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-pix_fmt", "yuv420p", "-c:a", "aac", "-ar", "48000",
                "-ac", "2", f"{index}.mp4",
            ], cwd=temp)
        listing = temp / "concat.txt"
        listing.write_text(
            "".join(f"file '{index}.mp4'\n" for index in range(len(clips))),
            encoding="utf-8",
        )
        _run([
            binary, "-hide_banner", "-loglevel", "error", "-y",
            "-f", "concat", "-safe", "0", "-i", listing.name,
            "-c", "copy", "-movflags", "+faststart", "output.mp4",
        ], cwd=temp)
        (temp / "output.mp4").replace(output)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--media-dir", required=True, type=Path)
    parser.add_argument("--manifest", type=Path, default=BASE_DIR / "media-manifest.json")
    parser.add_argument("--ffmpeg", default="ffmpeg")
    parser.add_argument("--font", type=Path)
    parser.add_argument("--output", type=Path, default=BASE_DIR / "demo.mp4")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        build(
            args.manifest, args.media_dir, args.output,
            ffmpeg=args.ffmpeg, font=args.font, dry_run=args.dry_run,
        )
    except MediaError as error:
        print(f"미디어 준비 실패: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
