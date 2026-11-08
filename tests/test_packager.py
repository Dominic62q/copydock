import shutil
import subprocess
import tarfile
from pathlib import Path

import pytest
import yaml

from copydock.analysis import analyze
from copydock.errors import CopyDockError
from copydock.manifest import ImageEntry, Manifest
from copydock.packager import (
    _checksums,
    _make_archive,
    bundle_conf,
    environment_template,
    render_compose,
    sha256_file,
)

ROOT = Path("/proj")


def config() -> dict:
    return {
        "name": "shop",
        "services": {
            "web": {
                "build": {"context": "/proj/web", "dockerfile": "Dockerfile"},
                "image": "shop-web:1",
                "environment": {"DB_PASS": "${DB_PASS:?set it}"},
            },
            "proxy": {
                "image": "caddy:2.8",
                "volumes": [
                    {"type": "bind", "source": "/proj/conf/Caddyfile", "target": "/etc/c"},
                    {"type": "volume", "source": "data", "target": "/data"},
                ],
            },
        },
        "volumes": {"data": {}},
    }


def test_render_compose_removes_builds_and_never_pulls():
    refs = {"web": "shop-web:1", "proxy": "caddy:2.8"}
    data, _ = render_compose(config(), refs, ROOT)
    for svc in data["services"].values():
        assert "build" not in svc
        assert svc["pull_policy"] == "never"
    assert data["services"]["web"]["image"] == "shop-web:1"


def test_render_compose_moves_bind_mounts_into_bundle():
    refs = {"web": "shop-web:1", "proxy": "caddy:2.8"}
    data, copies = render_compose(config(), refs, ROOT)
    binds = [v for v in data["services"]["proxy"]["volumes"] if v["type"] == "bind"]
    assert binds[0]["source"] == "./files/conf/Caddyfile"
    assert copies == {"files/conf/Caddyfile": Path("/proj/conf/Caddyfile")}


def test_render_compose_does_not_modify_input_and_keeps_variables():
    original = config()
    data, _ = render_compose(original, {"web": "a", "proxy": "b"}, ROOT)
    assert "build" in original["services"]["web"]
    dumped = yaml.safe_dump(data)
    assert "${DB_PASS:?set it}" in dumped  # filled in on the server, never at package time


def test_environment_template_lists_names_only():
    text = environment_template("shop", ["DB_PASS", "TOKEN"])
    assert "DB_PASS=\n" in text and "TOKEN=\n" in text


def test_environment_template_without_settings():
    assert "needs no settings" in environment_template("shop", [])


def test_checksums_cover_every_file(tmp_path):
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("world")
    sums = _checksums(tmp_path)
    assert set(sums) == {"a.txt", "sub/b.txt"}
    assert sums["a.txt"] == sha256_file(tmp_path / "a.txt")


def test_archive_has_one_top_folder_and_executable_installer(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "install.sh").write_text("#!/bin/sh\n")
    (staging / "compose.yaml").write_text("services: {}\n")
    archive = tmp_path / "out.tar.gz"
    _make_archive(staging, "shop-1-linux-amd64", archive)
    with tarfile.open(archive) as tar:
        names = tar.getnames()
        assert all(n.startswith("shop-1-linux-amd64") for n in names)
        assert tar.getmember("shop-1-linux-amd64/install.sh").mode == 0o755
        assert tar.getmember("shop-1-linux-amd64/compose.yaml").mode == 0o644
        assert tar.getmember("shop-1-linux-amd64/compose.yaml").uid == 0


def test_analysis_agrees_package_must_reject_env_file_and_variable_build_args():
    cfg = {
        "name": "p",
        "services": {
            "a": {"image": "x", "env_file": ["/proj/.env"]},
            "b": {"build": {"context": "/proj/b", "args": {"TOKEN": "${TOKEN}"}}},
        },
    }
    report = analyze(cfg, "", ROOT, "compose.yaml")
    text = " ".join(f.message for f in report.errors)
    assert "env_file" in text
    assert "build arg TOKEN" in text


def test_built_service_without_image_gets_compose_style_name():
    cfg = {"name": "p", "services": {"api": {"build": {"context": "/proj/api"}}}}
    report = analyze(cfg, "", ROOT, "compose.yaml")
    assert report.services[0].image == "p-api"


# ---- bundle.conf and install.sh ---------------------------------------------------------------

INSTALLER = Path(__file__).parent.parent / "src" / "copydock" / "templates" / "install.sh"


def manifest(**over) -> Manifest:
    base = {
        "app_name": "shop",
        "app_version": "1.0",
        "platform": "linux/amd64",
        "created_at": "x",
        "copydock_version": "0",
        "required_ports": [80, 443],
        "required_env": ["DB_PASS"],
        "images": [ImageEntry("caddy:2.8", ["proxy"], "sha256:a", None, False, 1)],
    }
    return Manifest(**{**base, **over})


def test_bundle_conf_is_sourceable_and_complete(tmp_path):
    conf = tmp_path / "bundle.conf"
    conf.write_text(bundle_conf(manifest()))
    out = subprocess.run(
        ["sh", "-c", f". {conf}; echo \"$APP_NAME|$PLATFORM|$REQUIRED_PORTS|$REQUIRED_ENV|$IMAGES\""],
        capture_output=True, text=True, check=True,
    ).stdout.strip()  # fmt: skip
    assert out == "shop|linux/amd64|80 443|DB_PASS|caddy:2.8"


@pytest.mark.parametrize(
    "over",
    [
        {"required_env": ["BAD NAME; rm -rf /"]},
        {"app_name": "Shop; echo hi"},
        {"images": [ImageEntry("x';touch /tmp/pwned;'", ["a"], "s", None, False, 1)]},
    ],
)
def test_bundle_conf_rejects_shell_unsafe_values(over):
    with pytest.raises(CopyDockError):
        bundle_conf(manifest(**over))


@pytest.mark.parametrize("shell", ["sh", "dash", "bash"])
def test_installer_syntax_is_valid_posix(shell):
    if shutil.which(shell) is None:
        pytest.skip(f"{shell} not installed")
    subprocess.run([shell, "-n", str(INSTALLER)], check=True)


def test_installer_help_runs_without_a_bundle():
    out = subprocess.run(["sh", str(INSTALLER), "--help"], capture_output=True, text=True, check=False)
    assert out.returncode == 0
    assert "--check" in out.stdout
