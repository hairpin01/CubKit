"""CubKit command line interface."""

from __future__ import annotations

import argparse
import shutil
import threading
from pathlib import Path
import sys
import textwrap
import time

from . import __version__
from .builder import build_project, check_project
from .errors import CubKitError
from .types_sync import sync_mcub_types


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
    parser = argparse.ArgumentParser(
        prog="cubkit", description="Toolkit for MCUB module development."
    )
    parser.add_argument("--version", action="version", version=f"cubkit {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init", help="create a starter MCUB module project"
    )
    init_parser.add_argument("path", type=Path)
    init_parser.add_argument(
        "--id", dest="module_id", help="module id; defaults to the directory name"
    )
    init_parser.set_defaults(func=_cmd_init)

    check_parser = subparsers.add_parser(
        "check", help="validate a CubKit module project"
    )
    check_parser.add_argument("path", type=Path, nargs="?", default=Path.cwd())
    check_parser.set_defaults(func=_cmd_check)

    build_parser = subparsers.add_parser(
        "build", help="build a single-file MCUB module artifact"
    )
    build_parser.add_argument("path", type=Path, nargs="?", default=Path.cwd())
    build_parser.add_argument("-o", "--output", type=Path, help="output .py path")
    build_parser.set_defaults(func=_cmd_build)

    types_parser = subparsers.add_parser(
        "types", help="download MCUB core/lib/types into a project"
    )
    types_parser.add_argument("path", type=Path, nargs="?", default=Path.cwd())
    types_parser.set_defaults(func=_cmd_types)
    return parser


def _cmd_init(args: argparse.Namespace) -> int:
    project_dir = args.path.resolve()
    module_id = args.module_id or _normalize_module_id(project_dir.name)
    package_name = f"{module_id}_lib"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / package_name).mkdir(exist_ok=True)
    (project_dir / "assets").mkdir(exist_ok=True)

    _write_new(
        project_dir / "cubkit.toml",
        _manifest_template(module_id, project_dir.name, package_name),
    )
    _write_new(project_dir / "main.py", _entrypoint_template(package_name))
    _write_new(
        project_dir / package_name / "__init__.py",
        '"""Private package for this MCUB module."""\n',
    )
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
    try:
        output = build_project(
            project_dir,
            args.output,
            progress=reporter.ok,
            status=reporter.status,
            dependency_progress=reporter.dependency,
        )
    finally:
        reporter.finish()
    print(f"📐 Done! in {_format_elapsed(time.monotonic() - reporter.started)}.")
    print(f"built: {output}")
    return 0


def _cmd_types(args: argparse.Namespace) -> int:
    project_dir = args.path.resolve()
    written = sync_mcub_types(project_dir)
    for path in written:
        try:
            label = path.relative_to(project_dir).as_posix()
        except ValueError:
            label = path.as_posix()
        print(f"OK {label}")
    print(f"types: downloaded {len(written)} file(s) into {project_dir / 'core' / 'lib' / 'types'}")
    print("gitignore: core/")
    return 0


class _ProgressReporter:
    """Small non-interactive progress printer for CubKit builds."""

    _SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    _BAR_WIDTH = 60
    _MIN_BAR_WIDTH = 8
    _REFRESH_INTERVAL = 0.1

    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.started = time.monotonic()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._active = False
        self._line_visible = False
        self._label = "building"
        self._index = 0
        self._total = 0
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def ok(self, path: Path, index: int, total: int) -> None:
        with self._lock:
            self._clear_line_locked()
            sys.stdout.write(f"OK {self._display_path(path)}\n")
            self._set_progress_locked("building", index, total)
            self._render_locked()

    def status(self, message: str) -> None:
        with self._lock:
            self._set_progress_locked(message, 0, 0)
            self._render_locked()

    def dependency(self, name: str, index: int, total: int) -> None:
        with self._lock:
            self._set_progress_locked(f"collecting dependencies... {name}", index, total)
            self._render_locked()

    def finish(self) -> None:
        self._stop.set()
        self._thread.join(timeout=1)
        with self._lock:
            self._clear_line_locked()
            self._active = False
            sys.stdout.flush()

    def _run(self) -> None:
        while not self._stop.wait(self._REFRESH_INTERVAL):
            with self._lock:
                if self._active:
                    self._render_locked()

    def _set_progress_locked(self, label: str, index: int, total: int) -> None:
        self._label = label
        self._index = index
        self._total = total
        self._active = True

    def _render_locked(self) -> None:
        self._clear_line_locked()
        sys.stdout.write(self._progress_line(self._label, self._index, self._total))
        sys.stdout.flush()
        self._line_visible = True

    def _clear_line_locked(self) -> None:
        if self._line_visible:
            sys.stdout.write("\r\033[2K")
            self._line_visible = False

    def _display_path(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.project_dir).as_posix()
        except ValueError:
            return path.as_posix()

    def _progress_line(self, label: str, index: int, total: int) -> str:
        elapsed = time.monotonic() - self.started
        spinner = self._SPINNER[int(elapsed * 10) % len(self._SPINNER)]
        elapsed_text = _format_elapsed(elapsed)
        suffix = "" if total <= 0 else f" {index}/{max(total, 1)}"
        label = _collapse_spaces(label)
        width = max(20, shutil.get_terminal_size((80, 20)).columns)
        line = self._format_progress_line(label, spinner, elapsed_text, suffix, index, total, width)
        return line

    def _format_progress_line(
        self,
        label: str,
        spinner: str,
        elapsed_text: str,
        suffix: str,
        index: int,
        total: int,
        width: int,
    ) -> str:
        label = _fit_label(label, max(6, min(len(label), width // 2)))
        prefix = f"{label} {spinner} {elapsed_text} "
        available = width - len(prefix) - len(suffix) - 3
        if available < self._MIN_BAR_WIDTH:
            label = _fit_label(label, max(0, len(label) + available - self._MIN_BAR_WIDTH))
            prefix = f"{label} {spinner} {elapsed_text} " if label else f"{spinner} {elapsed_text} "
            available = width - len(prefix) - len(suffix) - 3
        bar_width = max(1, min(self._BAR_WIDTH, available))
        bar = self._bar(index, total, bar_width)
        return f"{prefix}[{bar}]{suffix}"

    @staticmethod
    def _bar(index: int, total: int, width: int) -> str:
        if total <= 0:
            return ">" + "-" * (width - 1)
        total = max(total, 1)
        filled = max(0, int(width * index / total))
        filled = min(filled, width)
        if filled >= width:
            return "#" * width
        elif filled <= 0:
            return ">" + "-" * (width - 1)
        else:
            return "#" * filled + ">" + "-" * (width - filled - 1)


def _format_elapsed(seconds: float) -> str:
    if seconds < 10:
        return f"{seconds:.1f}s"
    return f"{seconds:.0f}s"


def _collapse_spaces(value: str) -> str:
    return " ".join(value.split())


def _fit_label(value: str, max_width: int) -> str:
    if max_width <= 0:
        return ""
    if len(value) <= max_width:
        return value
    if max_width == 1:
        return "…"
    return value[: max_width - 1] + "…"


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
    return textwrap.dedent(f"""
        id = {module_id!r}
        name = {name!r}
        version = "0.1.0"
        author = "unknown"
        description = "Built with CubKit"
        entrypoint = "main.py"
        # Optional output path for cubkit build when -o is not used.
        # out = "dist/{module_id}.py"
        package = {package_name!r}
        assets = "assets"
        """).lstrip()


def _entrypoint_template(package_name: str) -> str:
    return textwrap.dedent(f"""
        from {package_name}.utils import hello


        # Replace this file with your real MCUB module code.
        # CubKit keeps it as the entrypoint and embeds sibling package files.
        def cubkit_demo() -> str:
            return hello()
        """).lstrip()


def _utils_template() -> str:
    return textwrap.dedent("""
        def hello() -> str:
            return "Hello from CubKit"
        """).lstrip()


if __name__ == "__main__":
    raise SystemExit(main())
