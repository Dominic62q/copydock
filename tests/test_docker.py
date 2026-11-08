import pytest

from copydock import docker
from copydock.errors import CopyDockError


def fake_run(calls, fail_first_with=None):
    def run(args, what):
        calls.append(args)
        if fail_first_with and len(calls) == 1:
            raise CopyDockError(fail_first_with)
        return '[{"Id": "x"}]'

    return run


def test_platform_flag_goes_before_the_arguments(monkeypatch):
    calls = []
    monkeypatch.setattr(docker, "run", fake_run(calls))
    docker.save(["a:1", "b:2"], docker.Path("/tmp/x.tar"), "linux/arm64")
    docker.inspect_image("a:1", "linux/arm64")
    assert calls[0] == ["save", "--platform", "linux/arm64", "-o", "/tmp/x.tar", "a:1", "b:2"]
    assert calls[1] == ["image", "inspect", "--platform", "linux/arm64", "a:1"]


def test_falls_back_when_docker_does_not_know_the_flag(monkeypatch):
    calls = []
    monkeypatch.setattr(docker, "run", fake_run(calls, "Saving images failed:\nunknown flag: --platform"))
    docker.save(["a:1"], docker.Path("/tmp/x.tar"), "linux/amd64")
    assert calls[1] == ["save", "-o", "/tmp/x.tar", "a:1"]


def test_does_not_hide_other_errors(monkeypatch):
    calls = []
    monkeypatch.setattr(docker, "run", fake_run(calls, "no such image for platform linux/arm64"))
    with pytest.raises(CopyDockError, match="no such image"):
        docker.inspect_image("a:1", "linux/arm64")
    assert len(calls) == 1
