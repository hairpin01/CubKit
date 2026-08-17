"""High-level build orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from .bundler import build_zip_payload
from .collector import collect_bundle_files, validate_bundle_files
from .errors import BuildError
from .linter import LintIssue, lint_project
from .manifest import load_manifest
from .renderer import default_artifact_stem, render_module
from .security import validate_no_secrets

BuildProgress = Callable[[Path, int, int], None]
BuildStatus = Callable[[str], None]
DependencyProgress = Callable[[str, int, int], None]
LintOutput = Callable[[str], None]
LintProgress = Callable[[str, int, int], None]


def check_project(
    project_dir: Path,
    *,
    profile: str | None = None,
    run_configured_hooks: bool = True,
    status: BuildStatus | None = None,
    hook_output: Callable[[str], None] | None = None,
) -> int:
    """Validate a CubKit project and return the number of embedded files."""

    manifest = load_manifest(project_dir)
    if run_configured_hooks:
        from .hooks import run_hooks
        run_hooks(manifest, "pre_check", profile, status=status, output_callback=hook_output)
    if status is not None:
        status("collecting bundle files")
    files = collect_bundle_files(manifest)
    if status is not None:
        status("validating bundle files")
    validate_bundle_files(files)
    if manifest.fail_on_secrets:
        if status is not None:
            status("checking bundled files for secrets")
        validate_no_secrets(files)
    if run_configured_hooks:
        run_hooks(manifest, "post_check", profile, status=status, output_callback=hook_output)
    return len(files)


def build_project(
    project_dir: Path,
    output: Path | None = None,
    progress: BuildProgress | None = None,
    status: BuildStatus | None = None,
    dependency_progress: DependencyProgress | None = None,
    profile: str | None = None,
    run_configured_hooks: bool = True,
    hook_output: Callable[[str], None] | None = None,
    lint_output: LintOutput | None = None,
    lint_progress: LintProgress | None = None,
    reproducible: bool = False,
) -> Path:
    """Build *project_dir* and return the generated artifact path."""

    project_dir = project_dir.resolve()
    manifest = load_manifest(project_dir)
    if manifest.lint.auto:
        if status is not None:
            status("running lint")
        issues = lint_project(manifest, progress=lint_progress, profile=profile)
        _report_lint_issues(issues, lint_output)
        if any(issue.severity == "error" for issue in issues):
            raise BuildError("lint failed")
    if run_configured_hooks:
        from .hooks import run_hooks
        run_hooks(manifest, "pre_build", profile, status=status, output_callback=hook_output)
    if status is not None:
        status("collecting bundle files")
    if manifest.libs and status is not None:
        status("collecting dependencies")
    files = collect_bundle_files(manifest, dependency_progress=dependency_progress)
    total = 1 + len(files)
    done = 0

    def report(path: Path) -> None:
        nonlocal done
        done += 1
        if progress is not None:
            progress(path, done, total)

    report(manifest.entrypoint)
    if status is not None:
        status("validating bundle files")
    validate_bundle_files(files)
    if manifest.fail_on_secrets:
        if status is not None:
            status("checking bundled files for secrets")
        validate_no_secrets(files)
    for item in files:
        report(item.source)
    if status is not None:
        status("packing artifact")
    payload = build_zip_payload(files)
    if status is not None:
        status("rendering artifact")
    rendered = render_module(manifest, payload, bundle_files=files, reproducible=reproducible)
    if reproducible and rendered != render_module(
        manifest, payload, bundle_files=files, reproducible=True
    ):
        raise BuildError("reproducible build verification failed")

    if output is not None:
        output_path = output.resolve()
    elif profile == "debug" and manifest.debug_out is not None:
        output_path = manifest.debug_out
    elif profile == "release" and manifest.release_out is not None:
        output_path = manifest.release_out
    elif manifest.out is not None:
        output_path = manifest.out
    else:
        output_path = project_dir / "dist" / f"{default_artifact_stem(manifest)}.py"
    if status is not None:
        status("writing artifact")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
    if run_configured_hooks:
        from .hooks import run_hooks
        run_hooks(manifest, "post_build", profile, output_path, status=status, output_callback=hook_output)
    return output_path


def _report_lint_issues(issues: list[LintIssue], output: LintOutput | None) -> None:
    if output is None:
        return
    for issue in issues:
        path = issue.path.as_posix() if issue.path is not None else "main.py"
        links = f"  Fix: {issue.suggestion}\n  Docs: {issue.docs_url}\n"
        if issue.mcub_docs_url is not None:
            links += f"  MCUB: {issue.mcub_docs_url}\n"
        output(f"{issue.severity.upper()}[{issue.code}] {path}:{issue.line}: {issue.message}\n{links}")
