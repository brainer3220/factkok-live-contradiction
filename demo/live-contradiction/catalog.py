"""인물 ID를 검증된 설정·자막·영상에 연결한다."""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path
from typing import Any

import yaml

import pipeline as demo

PERSON_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
DEFAULT_PERSON_ID = "lee-jae-myung"


@dataclasses.dataclass(frozen=True)
class PersonProfile:
    """공개 메타데이터와 서버만 사용하는 검증된 파일 묶음."""

    person_id: str
    name: str
    portrait: str
    config: demo.DemoConfig | None = None
    segments: tuple[demo.Segment, ...] = ()
    config_path: Path | None = None
    transcript_path: Path | None = None
    video_path: Path | None = None

    @property
    def ready(self) -> bool:
        paths = (self.config_path, self.transcript_path, self.video_path)
        return self.config is not None and bool(self.segments) and all(
            path is not None and path.resolve() == path and path.is_file() for path in paths
        )

    def public_metadata(self) -> dict[str, Any]:
        return {
            "id": self.person_id,
            "name": self.name,
            "portrait": self.portrait,
            "status": "ready" if self.ready else "unavailable",
            "record_count": len(self.config.statements) if self.ready and self.config else 0,
        }


@dataclasses.dataclass(frozen=True)
class Catalog:
    default_person_id: str
    people: dict[str, PersonProfile]

    def public_metadata(self) -> dict[str, Any]:
        return {
            "default_person_id": self.default_person_id,
            "people": [profile.public_metadata() for profile in self.people.values()],
        }


def contained_file(base_dir: Path, raw: str, *, relative_to: Path | None = None) -> Path:
    """명시적으로 등록한 파일만 데모 디렉터리 안에서 찾는다."""
    path = Path(raw)
    if path.is_absolute() or any(part.startswith(".") for part in path.parts):
        raise demo.ConfigError("등록 파일은 데모 디렉터리 안의 상대 경로여야 합니다.")
    resolved = ((relative_to or base_dir) / path).resolve()
    if not resolved.is_relative_to(base_dir.resolve()):
        raise demo.ConfigError("등록 파일이 데모 디렉터리를 벗어납니다.")
    if not resolved.is_file():
        raise demo.ConfigError("등록 파일이 없습니다.")
    return resolved


def _load_profile(base_dir: Path, row: dict[str, Any]) -> PersonProfile:
    profile = PersonProfile(row["id"], row["name"], row["portrait"])
    if not row.get("config") or not row.get("transcript"):
        return profile
    try:
        config_path = contained_file(base_dir, row["config"])
        transcript_path = contained_file(base_dir, row["transcript"])
        config = demo.load_config(config_path)
        segments = tuple(demo.load_transcript(transcript_path))
        video_path = contained_file(base_dir, config.video, relative_to=config.base_dir)
        if config.holder != profile.name or not segments:
            return profile
        if not any(segment.speaker == config.holder for segment in segments):
            return profile
        for statement in config.statements:
            current = statement.get("current", {})
            index = current.get("segment_index")
            if type(index) is not int or not 0 <= index < len(segments):
                return profile
            segment = segments[index]
            if segment.speaker != config.holder or segment.text != current.get("text"):
                return profile
    except (OSError, ValueError, TypeError, KeyError, AttributeError, yaml.YAMLError):
        return profile
    return dataclasses.replace(
        profile,
        config=config,
        segments=segments,
        config_path=config_path,
        transcript_path=transcript_path,
        video_path=video_path,
    )


def load_catalog(base_dir: Path, *, config_path: Path | None = None) -> Catalog:
    """people.json을 읽는다. 파일이 없으면 기존 단일 인물 설정을 사용한다."""
    manifest = base_dir / "people.json"
    if manifest.is_file():
        data = json.loads(manifest.read_text(encoding="utf-8"))
    else:
        data = {
            "default_person_id": DEFAULT_PERSON_ID,
            "people": [{
                "id": DEFAULT_PERSON_ID,
                "name": demo.load_config(base_dir / "demo.yaml").holder,
                "portrait": "/assets/people/lee-jae-myung.jpg",
                "config": "demo.yaml",
                "transcript": "transcript.json",
            }],
        }
    if not isinstance(data, dict) or not isinstance(data.get("people"), list):
        raise demo.ConfigError("people.json에 people 목록이 필요합니다.")
    default_id = data.get("default_person_id")
    if not isinstance(default_id, str) or not PERSON_ID.fullmatch(default_id):
        raise demo.ConfigError("기본 인물 ID가 잘못됐습니다.")
    override = None
    if config_path is not None:
        try:
            override = str(config_path.resolve().relative_to(base_dir.resolve()))
        except ValueError:
            raise demo.ConfigError("--config는 데모 디렉터리 안의 파일이어야 합니다.") from None
    people = {}
    for raw in data["people"]:
        if not isinstance(raw, dict):
            raise demo.ConfigError("인물 정보는 객체여야 합니다.")
        person_id, name, portrait = raw.get("id"), raw.get("name"), raw.get("portrait")
        if not isinstance(person_id, str) or not PERSON_ID.fullmatch(person_id) or person_id in people:
            raise demo.ConfigError("인물 ID가 잘못됐거나 중복됩니다.")
        if not isinstance(name, str) or not name.strip() or len(name) > 100:
            raise demo.ConfigError("인물 이름이 잘못됐습니다.")
        if not isinstance(portrait, str) or not portrait.startswith("/assets/"):
            raise demo.ConfigError("인물 사진은 /assets/ 경로여야 합니다.")
        if any(part.startswith(".") for part in Path(portrait).parts):
            raise demo.ConfigError("인물 사진 경로가 잘못됐습니다.")
        row = dict(raw)
        if override is not None and person_id == default_id:
            row["config"] = override
        people[person_id] = _load_profile(base_dir, row)
    if default_id not in people:
        raise demo.ConfigError("기본 인물이 people 목록에 없습니다.")
    return Catalog(default_id, people)
