"""Thin wrapper around the `docker` command. All Docker work goes through here."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from copydock.errors import CopyDockError

SUPPORTED_PLATFORMS = ("linux/amd64", "linux/arm64")


def run(args: list[str], what: str) -> str:
    """Run `docker <args>`; on failure raise an error that shows the last lines of output."""
    try:
        result = subprocess.run(["docker", *args], capture_output=True, text=True, check=False)
    except FileNotFoundError:
        raise CopyDockError("Docker was not found. Install Docker and try again.") from None
    if result.returncode != 0:
        output = (result.stderr or result.stdout).strip().splitlines()
        raise CopyDockError(f"{what} failed:\n" + "\n".join(output[-15:]))
    return result.stdout


def server_platform() -> str:
    """Platform of the local Docker engine, e.g. linux/amd64. Also proves the daemon is up."""
    try:
        out = run(["version", "--format", "{{.Server.Os}}/{{.Server.Arch}}"], "Docker")
    except CopyDockError as e:
        if "not found" in str(e):
            raise
        raise CopyDockError(
            "Docker is installed but not running. Start Docker, then try again."
        ) from None
    return out.strip()


def build(ref: str, context: Path, dockerfile: str, platform: str, args: dict[str, str],
          target: str | None) -> None:  # fmt: skip
    cmd = ["build", "--platform", platform, "-t", ref, "-f", str(context / dockerfile)]
    for key, value in args.items():
        cmd += ["--build-arg", f"{key}={value}"]
    if target:
        cmd += ["--target", target]
    cmd.append(str(context))
    run(cmd, f"Building {ref}")


def pull(ref: str, platform: str) -> None:
    run(["pull", "--platform", platform, ref], f"Pulling {ref}")


def inspect_image(ref: str) -> dict:
    return json.loads(run(["image", "inspect", ref], f"Inspecting {ref}"))[0]


def save(refs: list[str], dest: Path) -> None:
    run(["save", "-o", str(dest), *refs], "Saving images")
