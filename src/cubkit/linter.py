"""AST-based checks for MCUB module entrypoints and external imports."""

from __future__ import annotations

import ast
import fnmatch
from functools import cache
import importlib.metadata
import importlib.util
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from packaging.requirements import InvalidRequirement, Requirement
from packaging.version import InvalidVersion, Version

if TYPE_CHECKING:
    from .manifest import LintConfig, Manifest

from .localization import load_locales
from .lint_docs import documentation_for_rule, suggestion_for_rule


@dataclass(frozen=True)
class LintIssue:
    line: int
    message: str
    severity: str = "error"
    code: str = "mcub-entrypoint"
    path: Path | None = None
    docs_url: str = ""
    mcub_docs_url: str | None = None
    suggestion: str = ""

    def __post_init__(self) -> None:
        docs_url, mcub_docs_url = documentation_for_rule(self.code)
        object.__setattr__(self, "docs_url", docs_url)
        object.__setattr__(self, "mcub_docs_url", mcub_docs_url)
        object.__setattr__(self, "suggestion", suggestion_for_rule(self.code))


def lint_project(
    manifest: "Manifest",
    *,
    check_imports: bool | None = None,
    strict_imports: bool | None = None,
    progress: Callable[[str, int, int], None] | None = None,
    tool_output: Callable[[str], None] | None = None,
    fix: bool = False,
    profile: str | None = None,
) -> list[LintIssue]:
    """Lint project sources, optionally validating every non-local import."""

    imports_enabled = manifest.lint.check_imports if check_imports is None else check_imports
    imports_strict = manifest.lint.strict_imports if strict_imports is None else strict_imports
    paths = [path for path in sorted(_iter_lintable_sources(manifest)) if not _path_ignored(path, manifest)]
    issues: list[LintIssue] = []
    local_modules = _local_top_level_modules(manifest.source_root)
    string_accesses: list[tuple[Path, int, str, set[str]]] = []
    dynamic_string_accesses: list[tuple[Path, int]] = []
    used_imports: set[str] = set()
    for index, path in enumerate(paths, start=1):
        if progress is not None:
            progress(f"linting {path.relative_to(manifest.project_dir).as_posix()}", index, len(paths))
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
        if path == manifest.entrypoint:
            issues.extend(lint_entrypoint(path))
        if _rule_enabled("decorators", manifest.lint):
            issues.extend(_lint_decorators(path, source))
        if _rule_enabled("async", manifest.lint):
            issues.extend(_lint_blocking_async_calls(path, tree))
            issues.extend(_lint_missing_await(path, tree))
            issues.extend(_lint_missing_cleanup(path, tree))
        if _rule_enabled("security", manifest.lint):
            issues.extend(_lint_hardcoded_secrets(path, tree))
        if _rule_enabled("assets", manifest.lint):
            issues.extend(_lint_assets(path, tree, manifest))
        if _rule_enabled("deprecated", manifest.lint):
            issues.extend(_lint_deprecated_mcub(path, tree))
        if _rule_enabled("locales", manifest.lint):
            string_accesses.extend(_find_string_accesses(path, source))
            dynamic_string_accesses.extend(_find_dynamic_string_accesses(path, tree))
        if imports_enabled:
            issues.extend(
                _lint_imports(path, source, manifest.lint, manifest.requires, manifest.libs, local_modules, imports_strict)
            )
        used_imports.update(module.split(".", 1)[0] for module, _ in _external_imports(tree))
    if _rule_enabled("locales", manifest.lint):
        issues.extend(_lint_locales(manifest, string_accesses, dynamic_string_accesses))
    if _rule_enabled("hooks", manifest.lint):
        issues.extend(_lint_hooks(manifest))
    if _rule_enabled("dependencies", manifest.lint):
        issues.extend(_lint_unused_requires(manifest, used_imports))
        issues.extend(_lint_dependency_versions(manifest))
    issues.extend(_lint_external_tools(manifest, progress, tool_output, fix, profile))
    return _apply_inline_ignores(issues, manifest)


def lint_entrypoint(path: Path) -> list[LintIssue]:
    """Return errors for a module that MCUB is unlikely to load."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module_classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and _is_module_class(node)]
    entrypoint_functions = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {"main", "register"}
    ]
    issues: list[LintIssue] = []
    if not module_classes and not entrypoint_functions:
        issues.append(LintIssue(1, "no ModuleBase/Module subclass, main(), or register() function found", path=path))
    if len(module_classes) > 1:
        issues.append(LintIssue(module_classes[1].lineno, "multiple MCUB module classes found", path=path))
    return issues


def _lint_decorators(path: Path, source: str) -> list[LintIssue]:
    tree = ast.parse(source, filename=str(path))
    issues: list[LintIssue] = []
    command_names: dict[str, int] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or not _is_module_class(node):
            continue
        for method in node.body:
            if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            decorators = {_expression_name(item) for item in method.decorator_list}
            active = decorators.intersection({"command", "callback", "watcher", "inline"})
            if not active:
                continue
            if not isinstance(method, ast.AsyncFunctionDef):
                issues.append(LintIssue(method.lineno, f"@{sorted(active)[0]} handler should be async", code="mcub-decorator", path=path))
            positional = [*method.args.posonlyargs, *method.args.args]
            if len(positional) < 2 or positional[0].arg != "self":
                issues.append(LintIssue(method.lineno, f"@{sorted(active)[0]} handler should accept self and event", code="mcub-handler-signature", path=path))
            for decorator in method.decorator_list:
                if _expression_name(decorator) != "command":
                    continue
                names = _command_names(decorator)
                if not names:
                    issues.append(LintIssue(method.lineno, "@command requires a non-empty literal command name", code="mcub-command", path=path))
                for name in names:
                    if name in command_names:
                        issues.append(LintIssue(method.lineno, f'duplicate command name "{name}" (first declared at line {command_names[name]})', code="mcub-command-conflict", path=path))
                    else:
                        command_names[name] = method.lineno
                if isinstance(decorator, ast.Call) and not any(
                    keyword.arg == "doc" or (keyword.arg or "").startswith("doc_")
                    for keyword in decorator.keywords
                ):
                    issues.append(LintIssue(method.lineno, "@command has no documentation", severity="warning", code="command-without-docs", path=path))
    return issues


def _command_names(decorator: ast.expr) -> set[str]:
    if not isinstance(decorator, ast.Call) or not decorator.args:
        return set()
    names: set[str] = set()
    first = decorator.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value:
        names.add(first.value)
    for keyword in decorator.keywords:
        if keyword.arg != "alias":
            continue
        if isinstance(keyword.value, ast.Constant) and isinstance(keyword.value.value, str):
            names.add(keyword.value.value)
        elif isinstance(keyword.value, (ast.List, ast.Tuple)):
            names.update(
                item.value for item in keyword.value.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            )
    return names


def _find_string_accesses(path: Path, source: str) -> list[tuple[Path, int, str, set[str]]]:
    tree = ast.parse(source, filename=str(path))
    accesses: list[tuple[Path, int, str, set[str]]] = []
    for node in ast.walk(tree):
        key: str | None = None
        kwargs: set[str] = set()
        if isinstance(node, ast.Subscript) and _is_self_strings(node.value):
            key = _literal_string(node.slice)
        elif isinstance(node, ast.Call) and _is_self_strings(node.func):
            key = _literal_string(node.args[0]) if node.args else None
            kwargs = {keyword.arg for keyword in node.keywords if keyword.arg}
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and _is_self_strings(node.func.value):
            key = _literal_string(node.args[0]) if node.args else None
        if key is not None:
            accesses.append((path, node.lineno, key, kwargs))
    return accesses


def _find_dynamic_string_accesses(path: Path, tree: ast.AST) -> list[tuple[Path, int]]:
    dynamic: list[tuple[Path, int]] = []
    for node in ast.walk(tree):
        argument: ast.expr | None = None
        if isinstance(node, ast.Subscript) and _is_self_strings(node.value):
            argument = node.slice
        elif isinstance(node, ast.Call) and _is_self_strings(node.func) and node.args:
            argument = node.args[0]
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" and _is_self_strings(node.func.value) and node.args:
            argument = node.args[0]
        if argument is not None and _literal_string(argument) is None:
            dynamic.append((path, node.lineno))
    return dynamic


def _lint_locales(
    manifest: "Manifest",
    accesses: list[tuple[Path, int, str, set[str]]],
    dynamic_accesses: list[tuple[Path, int]],
) -> list[LintIssue]:
    issues = [LintIssue(line, "dynamic locale key cannot be validated statically", severity="warning", code="locale-dynamic-key", path=path) for path, line in dynamic_accesses]
    if not accesses and manifest.locales is None:
        return issues
    if manifest.locales is None:
        issues.extend(LintIssue(line, f'locale key "{key}" is used but no locales directory is configured', severity="warning", code="locale-key", path=path) for path, line, key, _ in accesses)
        return issues
    locales = load_locales(manifest.locales)
    flat = {language: _flatten_locale(values) for language, values in locales.items()}
    used_keys = {key for _, _, key, _ in accesses}
    for path, line, key, provided in accesses:
        values = {language: entries[key] for language, entries in flat.items() if key in entries}
        missing = sorted(set(flat) - set(values))
        if not values:
            issues.append(LintIssue(line, f'locale key "{key}" does not exist', code="locale-key", path=path))
            continue
        if missing:
            issues.append(LintIssue(line, f'locale key "{key}" is missing in: {", ".join(missing)}', code="locale-key", path=path))
        expected = {frozenset(_format_placeholders(value)) for value in values.values()}
        if len(expected) > 1:
            issues.append(LintIssue(line, f'locale key "{key}" has inconsistent placeholders between locales', code="locale-placeholder", path=path))
        elif provided and expected:
            required = set(next(iter(expected)))
            missing_args = required - provided
            if missing_args:
                issues.append(LintIssue(line, f'locale key "{key}" is missing placeholders: {", ".join(sorted(missing_args))}', code="locale-placeholder", path=path))
    for language, entries in flat.items():
        locale_path = next(manifest.locales.glob(f"{language}.*"), manifest.locales)
        for key in sorted(set(entries) - used_keys):
            issues.append(LintIssue(1, f'locale key "{key}" is never used', severity="warning", code="locale-unused-key", path=locale_path))
    return issues


def _flatten_locale(values: dict[str, object], prefix: str = "") -> dict[str, str]:
    flattened: dict[str, str] = {}
    for key, value in values.items():
        name = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            flattened.update(_flatten_locale(value, name))
        elif isinstance(value, str):
            flattened[name] = value
    return flattened


def _format_placeholders(value: str) -> set[str]:
    return set(re.findall(r"(?<!\{)\{([a-zA-Z_][a-zA-Z0-9_]*)[^}]*\}(?!\})", value))


def _is_self_strings(node: ast.expr) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "strings" and isinstance(node.value, ast.Name) and node.value.id == "self"


def _literal_string(node: ast.expr) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _lint_hooks(manifest: "Manifest") -> list[LintIssue]:
    if manifest.hooks is None:
        return []
    issues: list[LintIssue] = []
    allowed_placeholders = {"project_dir", "manifest", "module_id", "command", "output", "profile"}
    hook_sets = [manifest.hooks.common, *manifest.hooks.profiles.values()]
    for events in hook_sets:
        for event, commands in events.items():
            for command in commands:
                for argument in command:
                    for placeholder in re.findall(r"\{([^{}]+)\}", argument):
                        if placeholder not in allowed_placeholders:
                            issues.append(LintIssue(1, f'unknown hook placeholder "{placeholder}" in {event}', code="hook-placeholder", path=manifest.project_dir / "cubkit.toml"))
                    if "{" in argument or "}" in argument or not _looks_like_project_path(argument):
                        continue
                    if not (manifest.project_dir / argument).exists():
                        issues.append(LintIssue(1, f'hook path does not exist: {argument}', code="hook-path", path=manifest.project_dir / "cubkit.toml"))
    return issues


def _looks_like_project_path(argument: str) -> bool:
    return "/" in argument or "\\" in argument or Path(argument).suffix in {".py", ".sh", ".bat"}


def _rule_enabled(rule: str, config: "LintConfig") -> bool:
    return rule not in config.disable and (not config.enable or rule in config.enable)


def _path_ignored(path: Path, manifest: "Manifest") -> bool:
    relative = path.relative_to(manifest.project_dir).as_posix()
    return any(fnmatch.fnmatchcase(relative, pattern) for pattern in manifest.lint.ignore)


def _apply_inline_ignores(issues: list[LintIssue], manifest: "Manifest") -> list[LintIssue]:
    filtered: list[LintIssue] = []
    source_cache: dict[Path, list[str]] = {}
    for issue in issues:
        if issue.path is None or not issue.path.is_file():
            filtered.append(issue)
            continue
        lines = source_cache.setdefault(issue.path, issue.path.read_text(encoding="utf-8").splitlines())
        line = lines[issue.line - 1] if issue.line <= len(lines) else ""
        if f"cubkit: ignore[{issue.code}]" not in line:
            filtered.append(issue)
    return filtered


def _lint_external_tools(
    manifest: "Manifest",
    progress: Callable[[str, int, int], None] | None,
    output: Callable[[str], None] | None,
    fix: bool,
    profile: str | None,
) -> list[LintIssue]:
    selected = manifest.lint.tools.for_profile(profile)
    tools = (
        ("ruff", selected.ruff),
        ("black", selected.black),
        ("mypy", selected.mypy),
    )
    issues: list[LintIssue] = []
    for name, enabled in tools:
        if not enabled:
            continue
        executable = shutil.which(name)
        if executable is None:
            issues.append(
                LintIssue(
                    1,
                    f"{name} is enabled but not installed",
                    severity="error" if manifest.lint.strict_tools else "warning",
                    code="tool-missing",
                    path=manifest.project_dir / "cubkit.toml",
                )
            )
            continue
        if progress is not None:
            progress(f"running {name}", 0, 0)
        command = _tool_command(name, executable, manifest.source_root, fix)
        result = subprocess.run(
            command,
            cwd=manifest.project_dir,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        if result.stdout and output is not None:
            output(f"[{name}]\n{result.stdout.rstrip()}\n")
        if result.returncode:
            issues.append(
                LintIssue(
                    1,
                    f"{name} reported problems",
                    code=f"tool-{name}",
                    path=manifest.project_dir / "cubkit.toml",
                )
            )
    return issues


def _tool_command(name: str, executable: str, source_root: Path, fix: bool) -> list[str]:
    if name == "ruff":
        return [executable, "check", str(source_root), *( ["--fix"] if fix else [] )]
    if name == "black":
        return [executable, str(source_root)] if fix else [executable, "--check", "--diff", str(source_root)]
    return [executable, str(source_root)]


def _lint_imports(
    path: Path,
    source: str,
    config: "LintConfig",
    requires: tuple[str, ...],
    libs: tuple[object, ...],
    local_modules: set[str],
    strict: bool,
) -> list[LintIssue]:
    tree = ast.parse(source, filename=str(path))
    issues: list[LintIssue] = []
    distributions = _installed_distributions()
    declared = {_normalize_distribution(requirement) for requirement in requires}
    declared.update(getattr(library, "name") for library in libs)
    aliases = config.import_aliases or {}

    for module, line in _external_imports(tree):
        top_level = module.split(".", 1)[0]
        if (
            module in {"cubkit.lib", "cubkit.libs"}
            or module.startswith(("cubkit.lib.", "cubkit.libs."))
            or top_level in local_modules
            or _is_builtin(top_level, (*config.mcub_modules, *config.runtime_modules))
        ):
            continue
        distribution = aliases.get(top_level) or _distribution_for_import(top_level, distributions)
        installed = _is_installed(top_level)
        declared_name = _normalize_distribution(distribution or top_level)
        is_declared = declared_name in declared or _normalize_distribution(top_level) in declared
        if not installed:
            issues.append(_import_issue(path, line, module, "is not installed", strict))
        elif not is_declared:
            issues.append(_import_issue(path, line, module, "is installed but absent from module.requires or libs", strict))
    return issues


def _lint_unused_requires(manifest: "Manifest", used_imports: set[str]) -> list[LintIssue]:
    aliases = manifest.lint.import_aliases or {}
    used_distributions = {
        _normalize_distribution(aliases.get(import_name, import_name))
        for import_name in used_imports
    }
    issues: list[LintIssue] = []
    for requirement in manifest.requires:
        distribution = _normalize_distribution(requirement)
        if distribution not in used_distributions:
            issues.append(
                LintIssue(
                    1,
                    f'"{requirement}" is declared in module.requires but never imported',
                    severity="warning",
                    code="unused-require",
                    path=manifest.project_dir / "cubkit.toml",
                )
            )
    return issues


def _lint_dependency_versions(manifest: "Manifest") -> list[LintIssue]:
    issues: list[LintIssue] = []
    for value in manifest.requires:
        try:
            requirement = Requirement(value)
        except InvalidRequirement:
            continue
        if not requirement.specifier:
            continue
        try:
            installed = Version(importlib.metadata.version(requirement.name))
        except (importlib.metadata.PackageNotFoundError, InvalidVersion):
            continue
        if installed not in requirement.specifier:
            issues.append(LintIssue(1, f'installed {requirement.name} {installed} does not satisfy {requirement.specifier}', code="dependency-version-mismatch", path=manifest.project_dir / "cubkit.toml"))
    return issues


def _lint_blocking_async_calls(path: Path, tree: ast.AST) -> list[LintIssue]:
    issues: list[LintIssue] = []
    blocked = {
        "time.sleep": "use await asyncio.sleep() instead of time.sleep() in async code",
        "requests.get": "use an async HTTP client instead of requests.get() in async code",
        "requests.post": "use an async HTTP client instead of requests.post() in async code",
        "requests.request": "use an async HTTP client instead of requests.request() in async code",
        "subprocess.run": "avoid subprocess.run() in async code",
        "subprocess.call": "avoid subprocess.call() in async code",
        "subprocess.check_output": "avoid subprocess.check_output() in async code",
        "open": "avoid synchronous open() in async code",
    }
    for async_function in (node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)):
        for node in ast.walk(async_function):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node.func)
            if name in blocked:
                issues.append(LintIssue(node.lineno, blocked[name], severity="warning", code="blocking-io", path=path))
    return issues


def _lint_missing_await(path: Path, tree: ast.AST) -> list[LintIssue]:
    async_names = {node.name for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)}
    known_methods = {"respond", "reply", "edit", "delete", "answer", "send_message", "send_file", "download_media"}
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
    issues: list[LintIssue] = []
    calls = {
        node
        for function in ast.walk(tree) if isinstance(function, ast.AsyncFunctionDef)
        for node in ast.walk(function) if isinstance(node, ast.Call)
    }
    for node in calls:
        name = _call_name(node.func)
        if name not in async_names and (name or "").rsplit(".", 1)[-1] not in known_methods:
            continue
        parent = parents.get(node)
        handled = False
        while parent is not None and not isinstance(parent, (ast.stmt, ast.AsyncFunctionDef)):
            if isinstance(parent, ast.Await) or (
                isinstance(parent, ast.Call)
                and (_call_name(parent.func) or "").rsplit(".", 1)[-1] in {"create_task", "gather"}
            ):
                handled = True
                break
            parent = parents.get(parent)
        if not handled:
            issues.append(LintIssue(node.lineno, f'async call "{name}" is not awaited', severity="warning", code="missing-await", path=path))
    return issues


def _lint_missing_cleanup(path: Path, tree: ast.AST) -> list[LintIssue]:
    issues: list[LintIssue] = []
    for node in (item for item in ast.walk(tree) if isinstance(item, ast.ClassDef) and _is_module_class(item)):
        creates_task = any(
            isinstance(call, ast.Call) and (_call_name(call.func) or "").rsplit(".", 1)[-1] == "create_task"
            for method in node.body if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
            for call in ast.walk(method)
        )
        has_cleanup = any(isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)) and method.name == "on_unload" for method in node.body)
        if creates_task and not has_cleanup:
            issues.append(LintIssue(node.lineno, f'class "{node.name}" creates background tasks but has no on_unload()', severity="warning", code="missing-cleanup", path=path))
    return issues


def _lint_hardcoded_secrets(path: Path, tree: ast.AST) -> list[LintIssue]:
    patterns = (
        re.compile(r"^\d{7,12}:[A-Za-z0-9_-]{20,}$"),
        re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
        re.compile(r"(?i)^(?:sk|api|token)[_-][A-Za-z0-9_-]{20,}$"),
    )
    return [
        LintIssue(node.lineno, "possible hardcoded credential in source", code="hardcoded-token", path=path)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and any(pattern.search(node.value) for pattern in patterns)
    ]


def _lint_assets(path: Path, tree: ast.AST, manifest: "Manifest") -> list[LintIssue]:
    issues: list[LintIssue] = []
    asset_methods = {"resource", "get_asset", "open_asset"}
    object_methods = {"assets.get", "assets.read_text", "assets.read_bytes", "assets.read_json"}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        name = _call_name(node.func) or ""
        if name.rsplit(".", 1)[-1] not in asset_methods and not any(name.endswith(f".{method}") or name == method for method in object_methods):
            continue
        relative = _literal_string(node.args[0])
        if relative is None:
            continue
        target = manifest.assets / relative if manifest.assets is not None else None
        if target is None or not target.is_file():
            issues.append(LintIssue(node.lineno, f'asset "{relative}" does not exist in the configured assets directory', code="asset-missing", path=path))
    return issues


def _lint_deprecated_mcub(path: Path, tree: ast.AST) -> list[LintIssue]:
    issues: list[LintIssue] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "register":
            arguments = [*node.args.posonlyargs, *node.args.args]
            if arguments and arguments[0].arg == "client":
                issues.append(LintIssue(node.lineno, "register(client) is deprecated; MCUB now passes kernel", severity="warning", code="deprecated-register-client", path=path))
        if isinstance(node, ast.Call) and _call_name(node.func) == "open" and node.args and _attribute_name(node.args[0]) == "kernel.CONFIG_FILE":
            issues.append(LintIssue(node.lineno, "direct access to kernel.CONFIG_FILE is deprecated", severity="warning", code="deprecated-config-file", path=path))
        if isinstance(node, ast.Call) and (_call_name(node.func) or "").rsplit(".", 1)[-1] == "ConfigValue" and len(node.args) >= 2:
            validator = next((keyword.value for keyword in node.keywords if keyword.arg == "validator"), None)
            if isinstance(validator, ast.Call) and any(keyword.arg == "default" for keyword in validator.keywords):
                issues.append(LintIssue(node.lineno, "validator default duplicates the default already provided by ConfigValue", severity="warning", code="redundant-validator-default", path=path))
    return issues


def _attribute_name(node: ast.expr) -> str | None:
    return _call_name(node)


def _call_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _call_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _external_imports(tree: ast.AST) -> list[tuple[str, int]]:
    imports: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((alias.name, node.lineno) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imports.append((node.module, node.lineno))
    return imports


def _import_issue(path: Path, line: int, module: str, detail: str, strict: bool) -> LintIssue:
    return LintIssue(
        line,
        f'import "{module}" {detail}',
        severity="error" if strict else "warning",
        code="external-import",
        path=path,
    )


def _iter_lintable_sources(manifest: "Manifest") -> list[Path]:
    """Avoid linting generated artifacts and virtual environments under source."""

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
        path for path in manifest.source_root.rglob("*.py")
        if not any(part in excluded_dirs for part in path.relative_to(manifest.source_root).parts)
    ]


def _local_top_level_modules(source_root: Path) -> set[str]:
    modules = {path.stem for path in source_root.rglob("*.py")}
    modules.update(path.parent.name for path in source_root.rglob("__init__.py"))
    return modules


def _is_builtin(name: str, mcub_modules: tuple[str, ...]) -> bool:
    return name in sys.stdlib_module_names or name in {*mcub_modules, "cubkit"}


@cache
def _is_installed(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


@cache
def _installed_distributions() -> dict[str, list[str]]:
    """Read installed-package metadata once, not once for every source file."""

    return importlib.metadata.packages_distributions()


def _distribution_for_import(name: str, distributions: dict[str, list[str]]) -> str | None:
    packages = distributions.get(name)
    return packages[0] if packages else None


def _normalize_distribution(value: str) -> str:
    name = re.split(r"[<>=!~\[; ]", value, maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", name).lower()


def _is_module_class(node: ast.ClassDef) -> bool:
    return any(_expression_name(base) in {"ModuleBase", "Module"} for base in node.bases)


def _has_decorator(node: ast.FunctionDef | ast.AsyncFunctionDef, name: str) -> bool:
    return any(_expression_name(decorator) == name for decorator in node.decorator_list)


def _expression_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _expression_name(node.func)
    return None
