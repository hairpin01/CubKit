"""CubKit command line interface."""

from __future__ import annotations

import argparse
import json
import shutil
import threading
from pathlib import Path
import sys
import textwrap
import time

from . import __version__
from .builder import build_project, check_project
from .errors import CubKitError
from .migration import migrate_manifest
from .manifest import load_manifest
from .linter import lint_project
from .types_sync import sync_mcub_types


def main(argv: list[str] | None = None) -> int:
    """Run the CubKit CLI."""

    parser = _make_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except CubKitError as exc:
        if getattr(args, "output_format", "text") == "json":
            print(json.dumps({"ok": False, "error": str(exc)}))
        else:
            print(f"ERROR {exc}", file=sys.stderr)
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
    init_parser.add_argument("--force", action="store_true", help="replace existing starter files")
    init_parser.set_defaults(func=_cmd_init)

    check_parser = subparsers.add_parser(
        "check", help="validate a CubKit module project"
    )
    check_parser.add_argument("path", type=Path, nargs="?", default=Path.cwd())
    _add_profile_arguments(check_parser)
    _add_output_arguments(check_parser, quiet=False)
    check_parser.set_defaults(func=_cmd_check)

    build_parser = subparsers.add_parser(
        "build", help="build a single-file MCUB module artifact"
    )
    build_parser.add_argument("path", type=Path, nargs="?", default=Path.cwd())
    build_parser.add_argument("-o", "--output", type=Path, help="output .py path")
    build_parser.add_argument("--reproducible", action="store_true", help="verify deterministic output and embed manifest hash")
    _add_profile_arguments(build_parser)
    _add_output_arguments(build_parser, quiet=True)
    build_parser.set_defaults(func=_cmd_build)

    lint_parser = subparsers.add_parser("lint", help="check an MCUB module entrypoint")
    lint_parser.add_argument("path", type=Path, nargs="?", default=Path.cwd())
    _add_profile_arguments(lint_parser)
    _add_output_arguments(lint_parser, quiet=False)
    lint_parser.add_argument("--check-imports", action="store_true", help="validate non-local imports")
    lint_parser.add_argument("--strict", action="store_true", help="treat import warnings as errors")
    lint_parser.add_argument("--fix", action="store_true", help="apply supported external tool fixes")
    lint_parser.set_defaults(func=_cmd_lint)

    types_parser = subparsers.add_parser(
        "types", help="download MCUB core/lib/types into a project"
    )
    types_parser.add_argument("path", type=Path, nargs="?", default=Path.cwd())
    types_parser.set_defaults(func=_cmd_types)

    migrate_parser = subparsers.add_parser(
        "migrate", help="migrate a legacy manifest to format 2"
    )
    migrate_parser.add_argument("path", type=Path, nargs="?", default=Path.cwd())
    migrate_parser.set_defaults(func=_cmd_migrate)
    return parser


def _add_profile_arguments(parser: argparse.ArgumentParser) -> None:
    profiles = parser.add_mutually_exclusive_group()
    profiles.add_argument("--debug", action="store_const", const="debug", dest="profile", help="use debug output and hooks")
    profiles.add_argument("--release", action="store_const", const="release", dest="profile", help="use release output and hooks")


def _add_output_arguments(parser: argparse.ArgumentParser, *, quiet: bool) -> None:
    parser.add_argument("--skip-hooks", action="store_true", help="do not run configured hooks")
    parser.add_argument("--format", choices=("text", "json"), dest="output_format", default="text", help="output format")
    parser.add_argument("--absolute-paths", action="store_true", help="show absolute paths")
    if quiet:
        parser.add_argument("--quiet", action="store_true", help="print only the output path")


def _cmd_init(args: argparse.Namespace) -> int:
    project_dir = args.path.resolve()
    module_id = args.module_id or _normalize_module_id(project_dir.name)
    package_name = f"{module_id}_lib"
    source_dir = project_dir / "src"
    project_dir.mkdir(parents=True, exist_ok=True)
    (source_dir / package_name).mkdir(parents=True, exist_ok=True)
    (project_dir / "assets").mkdir(exist_ok=True)

    written = _write_new(
        project_dir / "cubkit.toml",
        _manifest_template(module_id, project_dir.name, package_name),
        force=args.force,
    )
    written += _write_new(source_dir / "main.py", _entrypoint_template(package_name), force=args.force)
    written += _write_new(
        source_dir / package_name / "__init__.py",
        '"""Private package for this MCUB module."""\n',
        force=args.force,
    )
    written += _write_new(source_dir / package_name / "utils.py", _utils_template(), force=args.force)
    print(f"OK {len(written)} starter file(s) in {project_dir}")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    reporter = _ProgressReporter(args.path.resolve(), enabled=args.output_format == "text")
    try:
        count = check_project(args.path, profile=args.profile, run_configured_hooks=not args.skip_hooks, status=reporter.status, hook_output=reporter.capture_output)
    finally:
        reporter.finish()
    path = _display_path(args.path.resolve(), args.path.resolve(), args.absolute_paths)
    if args.output_format == "json":
        print(json.dumps({"ok": True, "path": str(args.path.resolve()), "embedded_files": count}))
    else:
        print(f"OK {path} ({count} embedded file(s))")
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    project_dir = args.path.resolve()
    reporter = _ProgressReporter(project_dir, enabled=args.output_format == "text" and not args.quiet)
    try:
        output = build_project(
            project_dir,
            args.output,
            progress=reporter.ok,
            status=reporter.status,
            dependency_progress=reporter.dependency,
            profile=args.profile,
            run_configured_hooks=not args.skip_hooks,
            hook_output=reporter.capture_output,
            lint_output=reporter.capture_output,
            lint_progress=reporter.progress,
            reproducible=args.reproducible,
        )
    finally:
        reporter.finish()
    displayed = _display_path(output, project_dir, args.absolute_paths)
    elapsed = time.monotonic() - reporter.started
    if args.output_format == "json":
        print(json.dumps({"ok": True, "output": str(output), "profile": args.profile or "default", "duration_seconds": round(elapsed, 3)}))
    elif args.quiet:
        print(displayed)
    else:
        print(f"📐 Done! in {_format_elapsed(elapsed)}.")
        print(f"Built {displayed}")
    return 0


def _cmd_lint(args: argparse.Namespace) -> int:
    manifest = load_manifest(args.path)
    issues = lint_project(
        manifest,
        check_imports=True if args.check_imports or args.strict else None,
        strict_imports=True if args.strict else None,
        tool_output=lambda text: print(text, end="", file=sys.stderr),
        fix=args.fix,
        profile=args.profile,
    )
    errors = [issue for issue in issues if issue.severity == "error"]
    if not issues:
        if args.output_format == "json":
            print(json.dumps({"ok": True, "path": str(manifest.entrypoint), "issues": []}))
        else:
            print(f"OK {_display_path(manifest.entrypoint, manifest.project_dir, args.absolute_paths)}")
        return 0
    if args.output_format == "json":
        print(json.dumps({"ok": not errors, "path": str(manifest.entrypoint), "issues": [issue.__dict__ for issue in issues]}, default=str))
    else:
        path = _display_path(manifest.entrypoint, manifest.project_dir, args.absolute_paths)
        for issue in issues:
            print(f"{issue.severity.upper()}[{issue.code}] {path}:{issue.line}: {issue.message}", file=sys.stderr)
            print(f"  Fix: {issue.suggestion}", file=sys.stderr)
            print(f"  Docs: {issue.docs_url}", file=sys.stderr)
            if issue.mcub_docs_url is not None:
                print(f"  MCUB: {issue.mcub_docs_url}", file=sys.stderr)
        print(f"lint: {len(errors)} error(s), {len(issues) - len(errors)} warning(s)", file=sys.stderr)
    return 1 if errors else 0


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


def _cmd_migrate(args: argparse.Namespace) -> int:
    path, backup, changed = migrate_manifest(args.path)
    if not changed:
        print(f"already format 2: {path}")
        return 0
    print(f"migrated: {path}")
    print(f"backup: {backup}")
    return 0


class _ProgressReporter:
    """TTY-aware build/check progress printer that keeps stdout script-friendly."""

    _SPINNER = "|/-\\"
    _BAR_WIDTH = 60
    _MIN_BAR_WIDTH = 8
    _REFRESH_INTERVAL = 0.1

    _MIN_VISIBLE_SECONDS = 0.1

    def __init__(self, project_dir: Path, *, enabled: bool = True) -> None:
        self.project_dir = project_dir
        self.started = time.monotonic()
        self.enabled = enabled
        self._interactive = enabled and sys.stderr.isatty()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._active = False
        self._line_visible = False
        self._label = "building"
        self._index = 0
        self._total = 0
        self._last_rendered_label: str | None = None
        self._visible_since: float | None = None
        self._captured_output: list[str] = []
        self._thread = threading.Thread(target=self._run, daemon=True)
        if self._interactive:
            self._thread.start()

    def ok(self, path: Path, index: int, total: int) -> None:
        with self._lock:
            if not self.enabled:
                return
            self._clear_line_locked()
            sys.stderr.write(f"OK {self._display_path(path)}\n")
            self._set_progress_locked("building", index, total)
            self._render_locked()

    def status(self, message: str) -> None:
        with self._lock:
            if not self.enabled:
                return
            if not self._interactive and message.startswith("linting "):
                message = "linting sources"
            self._set_progress_locked(message, 0, 0)
            self._render_locked()

    def progress(self, message: str, index: int, total: int) -> None:
        """Render a determinate stage such as linting project source files."""
        with self._lock:
            if not self.enabled:
                return
            if not self._interactive and message.startswith("linting "):
                message = "linting sources"
            self._set_progress_locked(message, index, total)
            self._render_locked()

    def dependency(self, name: str, index: int, total: int) -> None:
        with self._lock:
            self._set_progress_locked(f"collecting dependencies... {name}", index, total)
            self._render_locked()

    def finish(self) -> None:
        if self._interactive and self._visible_since is not None:
            remaining = self._MIN_VISIBLE_SECONDS - (time.monotonic() - self._visible_since)
            if remaining > 0:
                time.sleep(remaining)
        self._stop.set()
        if self._interactive:
            self._thread.join(timeout=1)
        with self._lock:
            self._clear_line_locked()
            self._active = False
            for output in self._captured_output:
                sys.stderr.write(output.rstrip("\n") + "\n")
            sys.stderr.flush()

    def capture_output(self, output: str) -> None:
        """Delay hook output until the progress line has been removed."""
        with self._lock:
            self._captured_output.append(output)

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
        if not self.enabled:
            return
        if not self._interactive:
            if self._label != self._last_rendered_label:
                sys.stderr.write(f"{self._label}\n")
                sys.stderr.flush()
                self._last_rendered_label = self._label
            return
        self._clear_line_locked()
        sys.stderr.write(self._progress_line(self._label, self._index, self._total))
        sys.stderr.flush()
        self._line_visible = True
        self._visible_since = time.monotonic()

    def _clear_line_locked(self) -> None:
        if self._line_visible:
            sys.stderr.write("\r\033[2K")
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
        return "."
    if max_width <= 3:
        return "." * max_width
    return value[: max_width - 3] + "..."


def _display_path(path: Path, project_dir: Path, absolute: bool) -> str:
    if absolute:
        return str(path.resolve())
    try:
        return path.resolve().relative_to(project_dir.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _write_new(path: Path, content: str, *, force: bool = False) -> list[Path]:
    if path.exists() and not force:
        return []
    path.write_text(content, encoding="utf-8")
    return [path]


def _normalize_module_id(value: str) -> str:
    chars = [char.lower() if char.isalnum() else "_" for char in value]
    module_id = "".join(chars).strip("_") or "my_module"
    if not module_id[0].isalpha():
        module_id = f"m_{module_id}"
    return module_id[:32]


def _manifest_template(module_id: str, name: str, package_name: str) -> str:
    return textwrap.dedent(f"""
        format = 2

        [module]
        id = {module_id!r}
        name = {name!r}
        version = "0.1.0"
        author = "unknown"
        description = "Built with CubKit"

        [bundle]
        source = "src"
        entrypoint = "main.py"
        # Optional output path for cubkit build when -o is not used.
        # output = "dist/{module_id}.py"
        packages = [{package_name!r}]
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
