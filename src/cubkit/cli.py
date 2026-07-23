"""CubKit command line interface."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import textwrap
import time

from . import __version__
from .builder import build_project, check_project
from .errors import CubKitError


def main(argv: list[str] | None = None) -> int:
    """Run the CubKit CLI."""

    parser = _make_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except CubKitError as exc:
        print(f"cubkit: error: {exc}", file=sys.stderr)
        return 1


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="cubkit", description="Toolkit for MCUB module development.")
    parser.add_argument("--version", action="version", version=f"cubkit {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="create a starter MCUB module project")
    init_parser.add_argument("path", type=Path)
    init_parser.add_argument("--id", dest="module_id", help="module id; defaults to the directory name")
    init_parser.set_defaults(func=_cmd_init)

    check_parser = subparsers.add_parser("check", help="validate a CubKit module project")
    check_parser.add_argument("path", type=Path, nargs="?", default=Path.cwd())
    check_parser.set_defaults(func=_cmd_check)

    build_parser = subparsers.add_parser("build", help="build a single-file MCUB module artifact")
    build_parser.add_argument("path", type=Path, nargs="?", default=Path.cwd())
    build_parser.add_argument("-o", "--output", type=Path, help="output .py path")
    build_parser.set_defaults(func=_cmd_build)
    return parser


def _cmd_init(args: argparse.Namespace) -> int:
    project_dir = args.path.resolve()
    module_id = args.module_id or _normalize_module_id(project_dir.name)
    package_name = f"{module_id}_lib"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / package_name).mkdir(exist_ok=True)
    (project_dir / "assets").mkdir(exist_ok=True)

    _write_new(project_dir / "cubkit.toml", _manifest_template(module_id, project_dir.name, package_name))
    _write_new(project_dir / "main.py", _entrypoint_template(package_name))
    _write_new(project_dir / package_name / "__init__.py", '"""Private package for this MCUB module."""\n')
    _write_new(project_dir / package_name / "utils.py", _utils_template())
    print(f"created CubKit module project: {project_dir}")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    count = check_project(args.path)
    print(f"ok: {args.path} ({count} embedded file(s))")
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    project_dir = args.path.resolve()
    reporter = _ProgressReporter(project_dir)
    output = build_project(project_dir, args.output, progress=reporter.ok)
    reporter.finish()
    print(f"built: {output}")
    return 0


class _ProgressReporter:
    """Small non-interactive progress printer for CubKit builds."""

    _SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    _BAR_WIDTH = 60

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.started = time.monotonic()
        self._progress_visible = False

    def ok(self, path: Path, index: int, total: int) -> None:
        if self._progress_visible:
            sys.stdout.write("\r\033[2K")
        sys.stdout.write(f"OK {self._display_path(path)}\n")
        sys.stdout.write(self._progress_line(index, total))
        sys.stdout.flush()
        self._progress_visible = True

    def finish(self) -> None:
        if self._progress_visible:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._progress_visible = False

    def _display_path(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.project_dir).as_posix()
        except ValueError:
            return path.as_posix()

    def _progress_line(self, index: int, total: int) -> str:
        total = max(total, 1)
        elapsed = time.monotonic() - self.started
        spinner = self._SPINNER[index % len(self._SPINNER)]
        filled = max(1, int(self._BAR_WIDTH * index / total))
        filled = min(filled, self._BAR_WIDTH)
        if filled >= self._BAR_WIDTH:
            bar = "#" * self._BAR_WIDTH
        else:
            bar = "#" * filled + ">" + "-" * (self._BAR_WIDTH - filled - 1)
        return f"{spinner} {_format_elapsed(elapsed)} [{bar}] {index}/{total}"


def _format_elapsed(seconds: float) -> str:
    if seconds < 10:
        return f"{seconds:.1f}s"
    return f"{seconds:.0f}s"


def _write_new(path: Path, content: str) -> None:
    if path.exists():
        return
    path.write_text(content, encoding="utf-8")


def _normalize_module_id(value: str) -> str:
    chars = [char.lower() if char.isalnum() else "_" for char in value]
    module_id = "".join(chars).strip("_") or "my_module"
    if not module_id[0].isalpha():
        module_id = f"m_{module_id}"
    return module_id[:32]


def _manifest_template(module_id: str, name: str, package_name: str) -> str:
    return textwrap.dedent(
        f'''
        id = {module_id!r}
        name = {name!r}
        version = "0.1.0"
        author = "unknown"
        description = "Built with CubKit"
        entrypoint = "main.py"
        package = {package_name!r}
        assets = "assets"
        '''
    ).lstrip()


def _entrypoint_template(package_name: str) -> str:
    return textwrap.dedent(
        f'''
        from {package_name}.utils import hello


        # Replace this file with your real MCUB module code.
        # CubKit keeps it as the entrypoint and embeds sibling package files.
        def cubkit_demo() -> str:
            return hello()
        '''
    ).lstrip()


def _utils_template() -> str:
    return textwrap.dedent(
        '''
        def hello() -> str:
            return "Hello from CubKit"
        '''
    ).lstrip()


if __name__ == "__main__":
    raise SystemExit(main())
