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
        {"app_version": "1'; touch /tmp/pwned; #"},
        {"app_version": "1\n"},
        {"platform": "linux/amd64'; touch /tmp/pwned; #"},
        {"platform": "windows/amd64"},
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


@pytest.fixture
def package_case(tmp_path, monkeypatch):
    from copydock import packager

    project = tmp_path / "project"
    project.mkdir()
    (project / "compose.yaml").write_text("services: {}\n")
    cfg = {"name": "shop", "services": {"web": {"image": "shop:1"}}}
    monkeypatch.setattr(packager, "load_config", lambda *a: cfg)
    monkeypatch.setattr(packager.docker, "server_platform", lambda: "linux/amd64")
    monkeypatch.setattr(packager.docker, "build", lambda *a: None)
    monkeypatch.setattr(packager.docker, "pull", lambda *a: None)
    monkeypatch.setattr(packager.docker, "inspect_image", lambda *a: {
        "Os": "linux", "Architecture": "amd64", "Id": "sha256:test",
    })
    monkeypatch.setattr(packager.docker, "save", lambda refs, path, platform: path.write_bytes(b"image"))
    monkeypatch.setattr(packager.docker, "run", lambda *a: None)

    def package(allow_secrets=False):
        return packager.package_project(
            project, None, "linux/amd64", tmp_path / "output", "1", allow_secrets
        )

    return project, cfg, package


def mount(cfg, source):
    cfg["services"]["web"]["volumes"] = [
        {"type": "bind", "source": str(source), "target": "/app"}
    ]


@pytest.mark.parametrize("filename", [".env", ".env.production", "tls/server.key", "tls/cert.pem"])
def test_package_rejects_nested_secrets_unless_explicitly_included(package_case, filename):
    project, cfg, package = package_case
    secret = project / filename
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("private-value")
    mount(cfg, project)
    with pytest.raises(CopyDockError, match="secret"):
        package()
    result = package(True)
    with tarfile.open(result.archive) as tar:
        member = f"shop-1-linux-amd64/files/{filename}"
        assert tar.extractfile(member).read() == b"private-value"


@pytest.mark.parametrize("kind", ["file", "directory", "dangling", "cycle"])
def test_package_rejects_symlinks_even_with_secret_override(package_case, kind):
    project, cfg, package = package_case
    outside = project.parent / "outside"
    if kind == "file":
        outside.write_text("outside-private-value")
    elif kind == "directory":
        outside.mkdir()
        (outside / "private").write_text("outside-private-value")
    elif kind == "cycle":
        outside = project
    (project / "link").symlink_to(outside, target_is_directory=kind in ("directory", "cycle"))
    mount(cfg, project)
    with pytest.raises(CopyDockError, match="[Ss]ymlink"):
        package(True)


def test_package_rejects_conflicting_builds_for_shared_image(package_case):
    project, cfg, package = package_case
    cfg["services"]["web"]["build"] = {"context": str(project), "dockerfile": "First"}
    cfg["services"]["worker"] = {
        "image": "shop:1", "build": {"context": str(project), "dockerfile": "Second"},
    }
    with pytest.raises(CopyDockError, match="shop:1"):
        package()


def test_package_allows_identical_builds_for_shared_image(package_case):
    project, cfg, package = package_case
    build = {"context": str(project)}
    cfg["services"]["web"]["build"] = build
    cfg["services"]["worker"] = {"image": "shop:1", "build": dict(build)}
    result = package()
    assert result.manifest.images[0].services == ["web", "worker"]


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("kind", ["file", "directory"])
def test_overlapping_mounts_fail_cleanly_in_cli(package_case, reverse, kind):
    from typer.testing import CliRunner

    from copydock.cli import app

    project, cfg, _ = package_case
    nested = project / "nested"
    if kind == "directory":
        nested.mkdir()
    else:
        nested.write_text("data")
    sources = [project, nested]
    if reverse:
        sources.reverse()
    cfg["services"]["web"]["volumes"] = [
        {"type": "bind", "source": str(source), "target": f"/mount{i}"}
        for i, source in enumerate(sources)
    ]
    result = CliRunner().invoke(app, ["package", str(project)])
    assert result.exit_code == 1
    assert "overlap" in result.output.lower()
    assert "Traceback" not in result.output
    assert isinstance(result.exception, SystemExit)


@pytest.mark.parametrize("unsafe", ["secret", "symlink"])
def test_package_rechecks_mounts_after_image_builds(package_case, monkeypatch, unsafe):
    from copydock import packager

    project, cfg, package = package_case
    mount(cfg, project)
    cfg["services"]["web"]["build"] = {"context": str(project)}

    def build(*args):
        if unsafe == "secret":
            (project / ".env").write_text("late-private-value")
        else:
            (project / "link").symlink_to(project)

    monkeypatch.setattr(packager.docker, "build", build)
    with pytest.raises(CopyDockError):
        package()
