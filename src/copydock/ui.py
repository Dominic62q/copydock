"""Everything the terminal shows. Commands call these helpers and never style text themselves,
so inspect, package and the rest look like one product."""

from __future__ import annotations

import time
from collections.abc import Generator
from contextlib import contextmanager

from rich import box
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from copydock import __version__
from copydock.analysis import Finding, Report

console = Console()
err_console = Console(stderr=True)

ACCENT = "cyan"
OK = "green"
WARN = "yellow"
BAD = "red"

TAGLINE = "package once, deploy anywhere"


def banner(subtitle: str | None = None) -> None:
    """Two crates on a dock, next to the name."""
    top = Text("  ▛▀▜▛▀▜  ", style=f"bold {ACCENT}")
    top.append("copydock", style="bold white")
    top.append(f"  v{__version__}", style="dim")
    bottom = Text("  ▙▄▟▙▄▟  ", style=f"bold {ACCENT}")
    bottom.append(subtitle or TAGLINE, style="dim")
    console.print()
    console.print(top)
    console.print(bottom)
    console.print()


class Step:
    """One line of progress. Set `detail` inside the block to show a short result on the right."""

    def __init__(self, title: str) -> None:
        self.title = title
        self.detail = ""
        self.notes: list[str] = []


def _step_line(icon: str, icon_style: str, step: Step, elapsed: float | None) -> Table:
    grid = Table.grid(padding=(0, 1), expand=True)
    grid.add_column(width=3)
    grid.add_column(no_wrap=True, min_width=30)
    grid.add_column(ratio=1, overflow="ellipsis", no_wrap=True)
    grid.add_column(justify="right", width=7)
    grid.add_row(
        Text(f" {icon}", style=icon_style),
        Text(step.title),
        Text(step.detail, style="dim"),
        Text("" if elapsed is None else f"{elapsed:.1f}s", style="dim"),
    )
    return grid


@contextmanager
def step(title: str) -> Generator[Step]:
    """Show a spinner while the block runs, then a result line: ✓ on success, ✗ on failure."""
    current = Step(title)
    started = time.monotonic()
    status = console.status(Text(title, style="dim"), spinner="dots", spinner_style=ACCENT)
    status.start()
    try:
        yield current
    except BaseException:
        status.stop()
        console.print(_step_line("✗", f"bold {BAD}", current, None))
        raise
    status.stop()
    console.print(_step_line("✓", f"bold {OK}", current, time.monotonic() - started))
    for note in current.notes:
        console.print(Text(f"     {note}", style=WARN))


def error(title: str, message: str) -> None:
    err_console.print()
    err_console.print(
        Panel(Text(message), title=f"[bold]{title}[/bold]", border_style=BAD, box=box.ROUNDED,
              padding=(1, 2), expand=False)
    )  # fmt: skip


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1000 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1000
    return f"{n:.1f} GB"


def print_findings(findings: list[Finding]) -> None:
    """Warnings first, then errors."""
    for f in (f for f in findings if f.level == "warning"):
        console.print(Text.assemble(("  ▲ ", f"bold {WARN}"), f.message))
    for f in (f for f in findings if f.level == "error"):
        console.print(Text.assemble(("  ✖ ", f"bold {BAD}"), f.message))


def print_report(report: Report) -> None:
    table = Table(box=box.ROUNDED, border_style="dim", header_style=f"bold {ACCENT}", expand=False)
    for column in ("Service", "Image", "Ports", "Mounts"):
        table.add_column(column, overflow="fold")
    for s in report.services:
        image = Text(s.image or "-")
        image.append("\n" + (f"build ./{s.build_context}" if s.build_context else "from registry"),
                     style="dim")  # fmt: skip
        mounts = Text()
        for m in s.bind_mounts:
            mounts.append(f"{m}\n")
        for v in s.named_volumes:
            mounts.append(f"{v}\n", style="dim")
        mounts.rstrip()
        table.add_row(
            Text(s.name, style="bold"),
            image,
            ", ".join(map(str, s.published_ports)) or "-",
            mounts if mounts.plain else "-",
        )
    console.print(table)

    needs = Table.grid(padding=(0, 2))
    needs.add_column(style="dim", justify="right")
    needs.add_column()
    needs.add_row("settings", ", ".join(report.required_env) or "none")
    needs.add_row("ports", ", ".join(map(str, report.published_ports)) or "none")
    console.print(Panel(needs, title="the server needs", title_align="left", border_style="dim",
                        box=box.ROUNDED, expand=False, padding=(0, 2)))  # fmt: skip

    if report.findings:
        console.print()
        print_findings(report.findings)

    console.print()
    if report.errors:
        n = len(report.errors)
        console.print(Text.assemble(("  ✖ ", f"bold {BAD}"),
                                    (f"{n} problem{'s' * (n != 1)} to fix before packaging", BAD)))  # fmt: skip
    else:
        console.print(Text.assemble(("  ✓ ", f"bold {OK}"), ("ready to package", OK)))
    console.print()


def print_bundle(archive_name: str, size: int, sha256: str, platform: str, images: list,
                 sidecar: str, next_steps: list[str]) -> None:  # fmt: skip
    table = Table(box=box.SIMPLE_HEAD, header_style="dim", pad_edge=False, expand=False)
    table.add_column("image")
    table.add_column("from", style="dim")
    table.add_column("size", justify="right")
    table.add_column("id", style="dim")
    for img in images:
        table.add_row(img.reference, "built" if img.built else "registry",
                      human_size(img.size_bytes), img.image_id.removeprefix("sha256:")[:12])  # fmt: skip

    facts = Table.grid(padding=(0, 2))
    facts.add_column(style="dim", justify="right")
    facts.add_column()
    facts.add_row("bundle", Text(archive_name, style="bold"))
    facts.add_row("size", human_size(size))
    facts.add_row("platform", Text(platform, style=f"bold {ACCENT}"))
    facts.add_row("sha256", f"{sha256[:16]}…  (full hash in {sidecar})")

    steps = Text()
    for i, line in enumerate(next_steps, 1):
        steps.append(f"{i}  ", style=f"bold {ACCENT}")
        steps.append(line + "\n")
    steps.rstrip()

    console.print()
    console.print(Panel(Group(facts, Text(), table, Text(), steps),
                        title=f"[bold {OK}]bundle ready[/bold {OK}]", title_align="left",
                        border_style=OK, box=box.ROUNDED, padding=(1, 2), expand=False))  # fmt: skip
    console.print()
