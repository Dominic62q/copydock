"""Bundle manifest: the packing list. What is inside a bundle, in a form the installer and
humans can check."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field

FORMAT_VERSION = 1


@dataclass
class ImageEntry:
    reference: str  # name:tag used in the rewritten compose file
    services: list[str]  # compose services that use this image
    image_id: str  # sha256 of the image config; identical on every machine that loads it
    repo_digest: str | None  # registry digest, for images pulled from a registry
    built: bool  # True if built from the project, False if pulled from a registry
    size_bytes: int


@dataclass
class Manifest:
    app_name: str
    app_version: str
    platform: str  # e.g. linux/amd64
    created_at: str  # UTC, ISO 8601
    copydock_version: str
    images_archive: str = "images/images.tar"
    images: list[ImageEntry] = field(default_factory=list)
    required_ports: list[int] = field(default_factory=list)
    required_env: list[str] = field(default_factory=list)  # names only, never values
    checksums: dict[str, str] = field(default_factory=dict)  # relative path -> sha256
    format_version: int = FORMAT_VERSION

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @classmethod
    def from_json(cls, text: str) -> Manifest:
        data = json.loads(text)
        if data.get("format_version") != FORMAT_VERSION:
            raise ValueError(
                f"Unsupported bundle format {data.get('format_version')!r}; "
                f"this CopyDock reads format {FORMAT_VERSION}."
            )
        images = [ImageEntry(**i) for i in data.pop("images", [])]
        return cls(images=images, **data)
