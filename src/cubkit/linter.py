"""Parse CubKit projects and orchestrate metadata-driven lint rules."""

from __future__ import annotations

import ast
import fnmatch
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Callable

from .errors import CubKitError
from .manifest import Manifest, find_manifest
from .rules import (
    LintIssue,
    RuleContext,
    SourceUnit,
    apply_inline_ignores,
    run_rules,
)


def lint_project(
    manifest: Manifest,
    *,
    check_imports: bool | None = None,
    strict_imports: bool | None = None,
    progress: Callable[[str, int, int], None] | None = None,
    tool_output: Callable[[str], None] | None = None,
    fix: bool = False,
    profile: str | None = None,
    only_paths: set[Path] | None = None,
    use_cache: bool = True,
) -> list[LintIssue]:
    """Parse every source file and execute enabled rules."""

    paths = [
        path
        for path in sorted(_iter_lintable_sources(manifest))
        if not _path_ignored(path, manifest)
    ]
    active_paths = (
        frozenset(path.resolve() for path in only_paths)
        if only_paths is not None
        else None
    )
    fingerprint = _cache_fingerprint(
        manifest,
        paths,
        check_imports=check_imports,
        strict_imports=strict_imports,
        profile=profile,
    )
    selected_tools = manifest.lint.tools.for_profile(profile)
    tools_enabled = selected_tools.ruff or selected_tools.black or selected_tools.mypy
    cache_enabled = use_cache and not fix and only_paths is None and not tools_enabled
    if cache_enabled:
        cached = _read_cache(manifest, fingerprint)
        if cached is not None:
            if progress is not None:
                progress("lint cache hit", 1, 1)
            return cached
    files: list[SourceUnit] = []
    progress_paths = [
        path for path in paths if active_paths is None or path.resolve() in active_paths
    ]
    progress_index = 0
    for path in paths:
        if progress is not None and path in progress_paths:
            progress_index += 1
            progress(
                f"linting {path.relative_to(manifest.project_dir).as_posix()}",
                progress_index,
                len(progress_paths),
            )
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        aliases = _collect_aliases(tree)
        files.append(
            SourceUnit(
                path=path,
                source=source,
                tree=tree,
                aliases=aliases,
                inferred_types=_infer_types(tree, aliases),
            )
        )

    context = RuleContext(
        manifest=manifest,
        files=tuple(files),
        imports_enabled=(
            manifest.lint.check_imports if check_imports is None else check_imports
        ),
        imports_strict=(
            manifest.lint.strict_imports if strict_imports is None else strict_imports
        ),
        progress=progress,
        tool_output=tool_output,
        fix=fix,
        profile=profile,
        active_paths=active_paths,
    )
    issues = apply_inline_ignores(run_rules(context), manifest)
    if cache_enabled:
        _write_cache(manifest, fingerprint, issues)
    return issues


def git_changed_files(manifest: Manifest) -> set[Path]:
    """Return changed and untracked Python files below the configured source root."""

    commands = (
        ["git", "diff", "--name-only", "--diff-filter=ACMR", "HEAD", "--"],
        ["git", "ls-files", "--others", "--exclude-standard"],
    )
    names: set[str] = set()
    for command in commands:
        result = subprocess.run(
            command,
            cwd=manifest.project_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if result.returncode and command[1] == "diff":
            continue
        if result.returncode:
            raise CubKitError(
                result.stderr.strip() or "failed to query changed Git files"
            )
        names.update(line for line in result.stdout.splitlines() if line)
    source_root = manifest.source_root.resolve()
    changed: set[Path] = set()
    for name in names:
        path = (manifest.project_dir / name).resolve()
        try:
            path.relative_to(source_root)
        except ValueError:
            continue
        if path.suffix == ".py" and path.is_file():
            changed.add(path)
    return changed


def _cache_fingerprint(
    manifest: Manifest,
    paths: list[Path],
    *,
    check_imports: bool | None,
    strict_imports: bool | None,
    profile: str | None,
) -> dict[str, object]:
    imports_enabled = (
        manifest.lint.check_imports if check_imports is None else check_imports
    )
    environment = ""
    if imports_enabled:
        packages = sorted(
            (distribution.metadata.get("Name", ""), distribution.version)
            for distribution in importlib.metadata.distributions()
        )
        environment = hashlib.sha256(repr(packages).encode()).hexdigest()
    return {
        "rules": hashlib.sha256(
            (Path(__file__).with_name("rules.py")).read_bytes()
        ).hexdigest(),
        "manifest": hashlib.sha256(
            find_manifest(manifest.project_dir).read_bytes()
        ).hexdigest(),
        "python": [sys.executable, list(sys.version_info[:3])],
        "environment": environment,
        "profile": profile,
        "check_imports": imports_enabled,
        "strict_imports": (
            manifest.lint.strict_imports if strict_imports is None else strict_imports
        ),
        "files": {
            path.relative_to(manifest.project_dir)
            .as_posix(): hashlib.sha256(path.read_bytes())
            .hexdigest()
            for path in paths
        },
    }


def _cache_path(manifest: Manifest) -> Path:
    root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    project_key = hashlib.sha256(
        str(manifest.project_dir.resolve()).encode()
    ).hexdigest()
    return root / "cubkit" / "lint" / f"{project_key}.json"


def _read_cache(
    manifest: Manifest, fingerprint: dict[str, object]
) -> list[LintIssue] | None:
    try:
        data = json.loads(_cache_path(manifest).read_text(encoding="utf-8"))
        if data.get("fingerprint") != fingerprint:
            return None
        return [
            LintIssue(
                line=item["line"],
                message=item["message"],
                severity=item["severity"],
                code=item["code"],
                path=(
                    (manifest.project_dir / item["path"]) if item.get("path") else None
                ),
            )
            for item in data.get("issues", [])
        ]
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError):
        return None


def _write_cache(
    manifest: Manifest, fingerprint: dict[str, object], issues: list[LintIssue]
) -> None:
    path = _cache_path(manifest)
    path.parent.mkdir(parents=True, exist_ok=True)
    serialized = []
    for issue in issues:
        issue_path = None
        if issue.path is not None:
            try:
                issue_path = (
                    issue.path.resolve().relative_to(manifest.project_dir).as_posix()
                )
            except ValueError:
                issue_path = issue.path.as_posix()
        serialized.append(
            {
                "line": issue.line,
                "message": issue.message,
                "severity": issue.severity,
                "code": issue.code,
                "path": issue_path,
            }
        )
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {"fingerprint": fingerprint, "issues": serialized}, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    temporary.replace(path)


def _collect_aliases(tree: ast.AST) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for item in node.names:
                aliases[item.asname or item.name.split(".", 1)[0]] = item.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for item in node.names:
                aliases[item.asname or item.name] = f"{node.module}.{item.name}"
    return aliases


def _infer_types(tree: ast.AST, aliases: dict[str, str]) -> dict[str, str]:
    inferred: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        value = node.value
        if not isinstance(value, ast.Call):
            continue
        call_name = _qualified_name(value.func)
        if call_name is None:
            continue
        root, separator, tail = call_name.partition(".")
        resolved = aliases.get(root, root) + (separator + tail if separator else "")
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        for target in targets:
            name = _target_name(target)
            if name is not None:
                inferred[name] = resolved
    for node in ast.walk(tree):
        if not isinstance(node, (ast.With, ast.AsyncWith)):
            continue
        for item in node.items:
            if (
                not isinstance(item.context_expr, ast.Call)
                or item.optional_vars is None
            ):
                continue
            call_name = _qualified_name(item.context_expr.func)
            target = _target_name(item.optional_vars)
            if call_name is None or target is None:
                continue
            root, separator, tail = call_name.partition(".")
            inferred[target] = aliases.get(root, root) + (
                separator + tail if separator else ""
            )
    return inferred


def _target_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _target_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _qualified_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _iter_lintable_sources(manifest: Manifest) -> list[Path]:
    excluded_dirs = {"__pycache__", ".git", ".venv", "venv", "build", "dist"}
    for output in (manifest.out, manifest.debug_out, manifest.release_out):
        if output is None:
            continue
        try:
            relative_parent = output.parent.relative_to(manifest.source_root)
        except ValueError:
            continue
        if relative_parent.parts:
            excluded_dirs.add(relative_parent.parts[0])
    return [
        path
        for path in manifest.source_root.rglob("*.py")
        if not any(
            part in excluded_dirs
            for part in path.relative_to(manifest.source_root).parts
        )
    ]


def _path_ignored(path: Path, manifest: Manifest) -> bool:
    relative = path.relative_to(manifest.project_dir).as_posix()
    return any(
        fnmatch.fnmatchcase(relative, pattern) for pattern in manifest.lint.ignore
    )
