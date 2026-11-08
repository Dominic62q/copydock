import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from copydock.analysis import Report, analyze
from copydock.cli import app

PROJECT = Path("/proj")
SAMPLE = Path(__file__).parent.parent / "examples" / "sample-app"


def run(config: dict, raw: str = "") -> Report:
    return analyze(config, raw, PROJECT, "compose.yaml")


def messages(report) -> str:
    return " | ".join(f.message for f in report.findings)


def test_ports_volumes_and_build_are_listed():
    report = run(
        {
            "services": {
                "web": {
                    "build": {"context": "/proj/web"},
                    "ports": [{"published": "8080", "target": 80}, {"target": 9000}],
                    "volumes": [
                        {"type": "volume", "source": "data", "target": "/data"},
                        {"type": "bind", "source": "/proj/conf/app.conf", "target": "/c"},
                    ],
                }
            }
        }
    )
    web = report.services[0]
    assert web.build_context == "web"
    assert web.published_ports == [8080]
    assert web.named_volumes == ["data"]
    assert web.bind_mounts == ["conf/app.conf"]
    assert report.findings == []


def test_required_env_found_in_raw_text():
    raw = "a: ${DB_PASS:?set it}\nb: ${TOKEN?}\nc: ${OPTIONAL:-x}\n"
    assert run({"services": {}}, raw).required_env == ["DB_PASS", "TOKEN"]


@pytest.mark.parametrize(
    "config, expected",
    [
        ({"volumes": {"v": {"external": True}}}, "external"),
        ({"networks": {"n": {"external": True}}}, "external"),
        ({"secrets": {"s": {}}}, "secret"),
        ({"configs": {"c": {}}}, "config"),
    ],
)
def test_unsupported_top_level_features_are_errors(config, expected):
    report = run({"services": {}, **config})
    assert len(report.errors) == 1
    assert expected in report.errors[0].message


def test_bind_mount_outside_project_is_error():
    report = run(
        {"services": {"a": {"volumes": [{"type": "bind", "source": "/etc/hosts", "target": "/h"}]}}}
    )
    assert "outside the project" in messages(report)
    assert report.errors


def test_docker_socket_is_error():
    report = run(
        {
            "services": {
                "a": {
                    "volumes": [
                        {"type": "bind", "source": "/var/run/docker.sock", "target": "/s"}
                    ]
                }
            }
        }
    )
    assert "Docker socket" in messages(report)


def test_build_context_outside_project_is_error():
    report = run({"services": {"a": {"build": {"context": "/elsewhere"}}}})
    assert "build context" in messages(report)


def test_secret_looking_bind_mount_warns():
    report = run(
        {"services": {"a": {"volumes": [{"type": "bind", "source": "/proj/.env", "target": "/e"}]}}}
    )
    assert report.errors == []
    assert "secrets" in report.warnings[0].message


def test_port_variable_warns_instead_of_crashing():
    report = run({"services": {"a": {"ports": [{"published": "${PORT}", "target": 80}]}}})
    assert report.services[0].published_ports == []
    assert len(report.warnings) == 1


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not installed")
def test_inspect_sample_app_end_to_end():
    result = CliRunner().invoke(app, ["inspect", str(SAMPLE), "--json"])
    assert result.exit_code == 0, result.output
    assert "POSTGRES_PASSWORD" in result.output
    assert "frontend" in result.output


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker CLI not installed")
def test_inspect_works_with_relative_project_path(monkeypatch):
    monkeypatch.chdir(SAMPLE.parent)
    result = CliRunner().invoke(app, ["inspect", SAMPLE.name, "--json"])
    assert result.exit_code == 0, result.output


def test_inspect_missing_compose_file(tmp_path):
    result = CliRunner().invoke(app, ["inspect", str(tmp_path)])
    assert result.exit_code == 1
    assert "No Compose file" in result.output
