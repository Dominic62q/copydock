import pytest

from copydock.manifest import ImageEntry, Manifest


def sample() -> Manifest:
    return Manifest(
        app_name="sample",
        app_version="1.0",
        platform="linux/amd64",
        created_at="2026-11-08T00:00:00Z",
        copydock_version="0.1.0",
        images=[
            ImageEntry("sample-backend:1.0", ["backend"], "sha256:abc", None, True, 123),
            ImageEntry("caddy:2.8", ["caddy"], "sha256:def", "caddy@sha256:123", False, 456),
        ],
        required_ports=[80],
        required_env=["POSTGRES_PASSWORD"],
        checksums={"compose.yaml": "deadbeef"},
    )


def test_roundtrip():
    m = sample()
    assert Manifest.from_json(m.to_json()) == m


def test_rejects_unknown_format_version():
    text = sample().to_json().replace('"format_version": 1', '"format_version": 99')
    with pytest.raises(ValueError, match="Unsupported bundle format"):
        Manifest.from_json(text)
