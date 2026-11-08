from __future__ import annotations

import json
from pathlib import Path

import typer

from copydock import __version__, ui
from copydock.analysis import analyze, find_compose_file, load_config
from copydock.errors import CopyDockError
from copydock.packager import package_project

app = typer.Typer(
    help="Package a Docker Compose app into a portable bundle for any Linux server.",
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode=None,
)


def _shorten(path: Path) -> str:
    """Show a path relative to the current folder when it lives below it."""
    try:
        return str(path.relative_to(Path.cwd()))
    except ValueError:
        return str(path)


def _version_callback(value: bool) -> None:
    if value:
        ui.console.print(f"copydock {__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True, help="Show version."
    ),
) -> None:
    pass


@app.command()
def inspect(
    project: Path = typer.Argument(..., exists=True, file_okay=False, help="Project directory."),
    compose: str = typer.Option(
        None, "--compose", "-f", help="Compose file name (default: auto-detect)."
    ),
    as_json: bool = typer.Option(False, "--json", help="Print the report as JSON."),
) -> None:
    """Show what a project needs to be packaged (images, ports, variables, unsupported features)."""
    if not as_json:
        ui.banner(f"inspecting {project.resolve().name}")
    try:
        compose_path = find_compose_file(project, compose)
        config = load_config(project, compose_path)
        report = analyze(config, compose_path.read_text(), project, compose_path.name)
    except CopyDockError as e:
        ui.error("Could not inspect", str(e))
        raise typer.Exit(1) from None

    if as_json:
        ui.console.print_json(json.dumps(report.to_dict()))
    else:
        ui.print_report(report)
    if report.errors:
        raise typer.Exit(1)


@app.command()
def package(
    project: Path = typer.Argument(..., exists=True, file_okay=False, help="Project directory."),
    compose: str = typer.Option(
        None, "--compose", "-f", help="Compose file name (default: auto-detect)."
    ),
    platform: str = typer.Option(
        None,
        "--platform",
        help="Platform of the SERVER: linux/amd64 or linux/arm64. "
        "Default: the platform of this machine's Docker.",
    ),
    app_version: str = typer.Option("latest", "--app-version", help="Version label for the bundle."),
    output: Path = typer.Option(Path("."), "--output", "-o", help="Folder for the bundle."),
    include_secret_files: bool = typer.Option(
        False,
        "--include-secret-files",
        help="Bundle bind-mounted files that look like secrets (.env, .pem, .key).",
    ),
) -> None:
    """Build a .tar.gz bundle that installs on a Linux server, online or offline."""
    ui.banner(f"packaging {project.resolve().name}")
    try:
        result = package_project(
            project, compose, platform, output, app_version, include_secret_files
        )
    except CopyDockError as e:
        ui.error("Could not package", str(e))
        raise typer.Exit(1) from None

    name = result.archive.name
    folder = name.removesuffix(".tar.gz")
    ui.print_bundle(
        archive_name=name,
        size=result.size,
        sha256=result.sha256,
        platform=result.manifest.platform,
        images=result.manifest.images,
        sidecar=result.sidecar.name,
        next_steps=[
            f"Copy to the server:  scp {_shorten(result.archive)} user@server:~/",
            f"On the server:       tar xzf {name} && cd {folder} && ./install.sh",
        ],
    )
    if result.findings:
        ui.print_findings(result.findings)
        ui.console.print()
