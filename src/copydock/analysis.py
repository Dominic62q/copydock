"""Read a Compose project and report what packaging it would need.

`load_config` asks Docker Compose to resolve the file (profiles, extends, merges, paths).
`analyze` is a pure function over that result, so it can be tested without Docker.

Anything v1 does not support is reported as an error rather than silently skipped (see V1.md).
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from copydock.errors import CopyDockError

COMPOSE_FILE_NAMES = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")

# ${VAR:?message} and ${VAR?message}: Compose refuses to run unless VAR is set.
_REQUIRED_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*):?\?")
_SECRET_FILE_HINTS = (".env", ".pem", ".key", ".p12", ".pfx")
_UNSUPPORTED_BUILD_KEYS = {"additional_contexts", "dockerfile_inline", "secrets", "ssh"}


@dataclass
class Finding:
    level: str  # "error" (v1 cannot package this) or "warning" (works, but check it)
    message: str
    code: str = ""  # machine-readable tag, e.g. "secret-file"


@dataclass
class ServiceInfo:
    name: str
    image: str | None
    build_context: str | None
    published_ports: list[int] = field(default_factory=list)
    bind_mounts: list[str] = field(default_factory=list)  # relative to the project
    named_volumes: list[str] = field(default_factory=list)


@dataclass
class Report:
    project: str
    compose_file: str
    services: list[ServiceInfo] = field(default_factory=list)
    required_env: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)

    @property
    def published_ports(self) -> list[int]:
        return sorted({p for s in self.services for p in s.published_ports})

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "warning"]

    def to_dict(self) -> dict:
        data = asdict(self)
        data["published_ports"] = self.published_ports
        return data


def find_compose_file(project: Path, name: str | None) -> Path:
    project = project.resolve()
    if name:
        path = project / name
        if not path.is_file():
            raise CopyDockError(f"Compose file not found: {path}")
        return path
    for candidate in COMPOSE_FILE_NAMES:
        if (project / candidate).is_file():
            return project / candidate
    raise CopyDockError(
        f"No Compose file in {project}. Looked for: {', '.join(COMPOSE_FILE_NAMES)}. "
        "Use --compose to name it."
    )


def load_config(project: Path, compose_file: Path) -> dict:
    """Resolve the Compose file with `docker compose config`.

    Interpolation is left off on purpose: values like ${POSTGRES_PASSWORD} are filled in on the
    destination server, never at package time, so inspect must not need them.
    """
    project = project.resolve()  # also used as cwd, so a relative path would be applied twice
    compose_file = compose_file.resolve()
    cmd = [
        "docker", "compose",
        "--project-directory", str(project),
        "-f", str(compose_file),
        "--profile", "*",
        "config", "--no-interpolate", "--format", "json",
    ]  # fmt: skip
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=project, check=False)
    except FileNotFoundError:
        raise CopyDockError("Docker was not found. Install Docker with the Compose plugin.") from None
    if result.returncode != 0:
        detail = result.stderr.strip() or "no details from docker"
        raise CopyDockError(f"`docker compose config` failed for {compose_file.name}:\n{detail}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise CopyDockError("Could not read the output of `docker compose config`.") from None


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _relative(path: Path, root: Path) -> str:
    return str(path.resolve().relative_to(root.resolve()))


def analyze(config: dict, raw_text: str, project: Path, compose_file: str) -> Report:
    report = Report(project=str(project), compose_file=compose_file)
    root = project.resolve()
    project_name = config.get("name") or root.name.lower()

    # Required variables are found in the raw text: the resolved config does not list them.
    # Limitation: variables used only in `include:`d or `extends:`d files are not seen.
    report.required_env = sorted(set(_REQUIRED_VAR.findall(raw_text)))

    for kind in ("volumes", "networks"):
        for name, spec in (config.get(kind) or {}).items():
            if spec and spec.get("external"):
                report.findings.append(
                    Finding("error", f"{kind[:-1].capitalize()} '{name}' is external. "
                            "v1 only packages volumes and networks that Compose creates itself.")
                )  # fmt: skip
    for kind in ("secrets", "configs"):
        for name in config.get(kind) or {}:
            report.findings.append(
                Finding("error", f"Top-level {kind[:-1]} '{name}' is not supported in v1. "
                        "Pass the value as an environment variable instead.")
            )  # fmt: skip

    for name, svc in (config.get("services") or {}).items():
        # A built service with no `image:` gets the name Compose would give it.
        image = svc.get("image") or (f"{project_name}-{name}" if svc.get("build") else None)
        info = ServiceInfo(name=name, image=image, build_context=None)

        build = svc.get("build")
        if build:
            context = Path(build["context"] if isinstance(build, dict) else build)
            for key in _UNSUPPORTED_BUILD_KEYS & set(build if isinstance(build, dict) else ()):
                report.findings.append(
                    Finding("error", f"{name}: build option '{key}' is not supported in v1.")
                )
            for arg, value in ((build.get("args") or {}) if isinstance(build, dict) else {}).items():
                if value is not None and "$" in str(value):
                    report.findings.append(
                        Finding("error", f"{name}: build arg {arg} uses a variable. Build args "
                                "must be fixed values, because images are built before install.")
                    )  # fmt: skip
            if _inside(context, root):
                info.build_context = _relative(context, root) or "."
            else:
                info.build_context = str(context)
                report.findings.append(
                    Finding("error", f"{name}: build context {context} is outside the project.")
                )

        for port in svc.get("ports") or []:
            published = port.get("published") if isinstance(port, dict) else None
            if published is None:
                continue  # container-only port, nothing opens on the host
            try:
                info.published_ports.append(int(published))
            except ValueError:
                report.findings.append(
                    Finding("warning", f"{name}: port '{published}' is a range or uses a variable; "
                            "it was not included in the port check.")
                )  # fmt: skip

        for vol in svc.get("volumes") or []:
            if vol.get("type") == "volume":
                info.named_volumes.append(vol.get("source") or "(anonymous)")
            elif vol.get("type") == "bind":
                source = Path(vol["source"])
                if source.name == "docker.sock":
                    report.findings.append(
                        Finding("error", f"{name}: mounts the Docker socket. That gives the "
                                "container control of the host, and v1 does not package it.")
                    )  # fmt: skip
                elif not _inside(source, root):
                    report.findings.append(
                        Finding("error", f"{name}: bind mount {source} is outside the project, "
                                "so it cannot be bundled.")
                    )  # fmt: skip
                else:
                    rel = _relative(source, root)
                    info.bind_mounts.append(rel)
                    if source.name.startswith(".env") or source.suffix in _SECRET_FILE_HINTS:
                        report.findings.append(
                            Finding("warning", f"{name}: bind mount {rel} looks like it may "
                                    "hold secrets. Check it before bundling.", "secret-file")
                        )  # fmt: skip

        if svc.get("env_file"):
            report.findings.append(
                Finding("error", f"{name}: uses env_file, which is not supported in v1. Move the "
                        "values into `environment:` as ${VARIABLES}, so the server provides them.")
            )  # fmt: skip
        if svc.get("privileged"):
            report.findings.append(Finding("warning", f"{name}: runs privileged."))
        if svc.get("network_mode") == "host":
            report.findings.append(Finding("warning", f"{name}: uses host networking."))

        report.services.append(info)

    return report
