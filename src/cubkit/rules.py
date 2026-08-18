"""AST-based checks for MCUB module entrypoints and external imports."""

from __future__ import annotations

import ast
from functools import cache
import importlib.metadata
import importlib.util
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class SourceUnit:
    path: Path
    source: str
    tree: ast.AST
    aliases: dict[str, str]
    inferred_types: dict[str, str]


@dataclass
class RuleContext:
    manifest: "Manifest"
    files: tuple[SourceUnit, ...]
    imports_enabled: bool
    imports_strict: bool
    progress: Callable[[str, int, int], None] | None = None
    tool_output: Callable[[str], None] | None = None
    fix: bool = False
    profile: str | None = None
    active_paths: frozenset[Path] | None = None
    cache: dict[str, list[LintIssue]] = field(default_factory=dict)


RuleCheck = Callable[[RuleContext], list[LintIssue]]


@dataclass(frozen=True)
class LintRule:
    id: str
    level: str
    description: str
    fix: str
    group: str
    check: RuleCheck


def run_rules(context: RuleContext) -> list[LintIssue]:
    """Run enabled metadata-rich rule objects."""

    issues: list[LintIssue] = []
    for rule in RULES:
        enabled = rule.group in {"entrypoint", "imports", "tools"} or _rule_enabled(
            rule.group, context.manifest.lint
        )
        if not enabled:
            continue
        issues.extend(rule.check(context))
    return issues


def apply_inline_ignores(
    issues: list[LintIssue], manifest: "Manifest"
) -> list[LintIssue]:
    return _apply_inline_ignores(issues, manifest)


def lint_entrypoint(
    path: Path, known_classes: set[str] | None = None
) -> list[LintIssue]:
    """Return errors for a module that MCUB is unlikely to load."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module_classes = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and _is_module_class(node, known_classes)
    ]
    entrypoint_functions = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in {"main", "register"}
    ]
    issues: list[LintIssue] = []
    if not module_classes and not entrypoint_functions:
        issues.append(
            LintIssue(
                1,
                "no ModuleBase/Module subclass, main(), or register() function found",
                path=path,
            )
        )
    if len(module_classes) > 1:
        issues.append(
            LintIssue(
                module_classes[1].lineno,
                "multiple MCUB module classes found",
                path=path,
            )
        )
    return issues


def _lint_decorators(
    path: Path,
    tree: ast.AST,
    manifest: "Manifest",
    known_classes: set[str] | None = None,
) -> list[LintIssue]:
    issues: list[LintIssue] = []
    command_names: dict[tuple[str, str], int] = {}
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    inline_used = False
    handler_decorators = {
        "command",
        "bot_command",
        "callback",
        "watcher",
        "inline",
        "event",
    }
    lifecycle_signatures = {
        "on_load": 1,
        "on_install": 1,
        "uninstall": 1,
        "on_uninstall": 1,
        "method": 1,
    }
    class_lifecycle_signatures = {
        "on_load": 1,
        "on_unload": 1,
        "on_install": 1,
        "on_reload": 1,
        "on_config_update": 4,
        "on_language_change": 2,
    }
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        module_class = _enclosing_module_class(node, parents, known_classes)
        decorators: dict[str, ast.expr] = {}
        for item in node.decorator_list:
            short_name = _expression_name(item)
            qualified_name = _qualified_name(item) or ""
            if short_name and (
                module_class is not None
                or ".register." in qualified_name
                or qualified_name.startswith("kernel.register.")
            ):
                decorators[short_name] = item
        active = set(decorators).intersection(handler_decorators)
        positional = [*node.args.posonlyargs, *node.args.args]
        if active:
            kind = sorted(active)[0]
            if not isinstance(node, ast.AsyncFunctionDef):
                issues.append(
                    LintIssue(
                        node.lineno,
                        f"@{kind} handler should be async",
                        code="mcub-decorator",
                        path=path,
                    )
                )
            expected = 2 if module_class is not None else 1
            first = "self" if module_class is not None else "event"
            if len(positional) < expected or positional[0].arg != first:
                signature = "self and event" if module_class is not None else "event"
                issues.append(
                    LintIssue(
                        node.lineno,
                        f"@{kind} handler should accept {signature}",
                        code="mcub-handler-signature",
                        path=path,
                    )
                )
            event_arg = (
                positional[1]
                if module_class is not None and len(positional) > 1
                else (positional[0] if positional else None)
            )
            expected_type = "InlineMessage" if "callback" in active else "Event"
            if event_arg is not None and expected_type not in _annotation_names(
                event_arg.annotation
            ):
                issues.append(
                    LintIssue(
                        node.lineno,
                        f'handler parameter "{event_arg.arg}" should be annotated as {expected_type}',
                        severity="warning",
                        code="missing-handler-types",
                        path=path,
                    )
                )
        for kind in ("command", "bot_command"):
            decorator = decorators.get(kind)
            if decorator is None:
                continue
            names = _command_names(decorator)
            primary = (
                _literal_string(decorator.args[0])
                if isinstance(decorator, ast.Call) and decorator.args
                else None
            )
            if not primary:
                issues.append(
                    LintIssue(
                        node.lineno,
                        f"@{kind} requires a non-empty literal command name",
                        code="mcub-command",
                        path=path,
                    )
                )
            namespace = "bot" if kind == "bot_command" else "user"
            for name in names:
                key = (namespace, name)
                if key in command_names:
                    issues.append(
                        LintIssue(
                            node.lineno,
                            f'duplicate command name "{name}" (first declared at line {command_names[key]})',
                            code="mcub-command-conflict",
                            path=path,
                        )
                    )
                else:
                    command_names[key] = node.lineno
            if isinstance(decorator, ast.Call) and not any(
                keyword.arg == "doc" or (keyword.arg or "").startswith("doc_")
                for keyword in decorator.keywords
            ):
                issues.append(
                    LintIssue(
                        node.lineno,
                        f"@{kind} has no documentation",
                        severity="warning",
                        code="command-without-docs",
                        path=path,
                    )
                )
        if module_class is not None and node.name in class_lifecycle_signatures:
            issues.extend(
                _validate_lifecycle_signature(
                    path, node, class_lifecycle_signatures[node.name], "self"
                )
            )
        for lifecycle, expected in lifecycle_signatures.items():
            if lifecycle in decorators and module_class is None:
                issues.extend(
                    _validate_lifecycle_signature(path, node, expected, "kernel")
                )
        if "event" in decorators:
            issues.extend(_lint_event_decorator(path, node, decorators["event"]))
        if "watcher" in decorators:
            issues.extend(_lint_watcher_decorator(path, node, decorators["watcher"]))
        if "loop" in decorators:
            issues.extend(_lint_loop_decorator(path, node, decorators["loop"]))
        inline_used = inline_used or bool(active.intersection({"inline", "callback"}))
    inline_used = inline_used or any(
        isinstance(node, ast.Call)
        and (
            (_call_name(node.func) or "").rsplit(".", 1)[-1]
            in {"inline_temp", "rich_form"}
            or (_call_name(node.func) or "").endswith(".inline.form")
        )
        for node in ast.walk(tree)
    )
    if inline_used and manifest.scop != "inline":
        issues.append(
            LintIssue(
                1,
                "module uses inline APIs but module.scope is not inline",
                severity="warning",
                code="inline-scope-missing",
                path=path,
            )
        )
    issues.extend(_lint_manual_handlers_and_inline(path, tree))
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
        if isinstance(keyword.value, ast.Constant) and isinstance(
            keyword.value.value, str
        ):
            names.add(keyword.value.value)
        elif isinstance(keyword.value, (ast.List, ast.Tuple)):
            names.update(
                item.value
                for item in keyword.value.elts
                if isinstance(item, ast.Constant) and isinstance(item.value, str)
            )
    return names


def _enclosing_module_class(
    node: ast.AST,
    parents: dict[ast.AST, ast.AST],
    known_classes: set[str] | None = None,
) -> ast.ClassDef | None:
    parent = parents.get(node)
    while parent is not None:
        if isinstance(parent, ast.ClassDef):
            return parent if _is_module_class(parent, known_classes) else None
        parent = parents.get(parent)
    return None


def _validate_lifecycle_signature(
    path: Path,
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    expected_count: int,
    first_name: str,
) -> list[LintIssue]:
    issues: list[LintIssue] = []
    positional = [*node.args.posonlyargs, *node.args.args]
    if not isinstance(node, ast.AsyncFunctionDef):
        issues.append(
            LintIssue(
                node.lineno,
                f"lifecycle callback {node.name} should be async",
                code="invalid-lifecycle-signature",
                path=path,
            )
        )
    if (
        len(positional) != expected_count
        or not positional
        or positional[0].arg != first_name
    ):
        issues.append(
            LintIssue(
                node.lineno,
                f"lifecycle callback {node.name} has an invalid signature",
                code="invalid-lifecycle-signature",
                path=path,
            )
        )
    return issues


def _lint_event_decorator(
    path: Path, node: ast.AST, decorator: ast.expr
) -> list[LintIssue]:
    if not isinstance(decorator, ast.Call) or not decorator.args:
        return []
    event_type = _literal_string(decorator.args[0])
    if event_type is None:
        return []
    valid = {
        "newmessage",
        "message",
        "messageedited",
        "edited",
        "messagedeleted",
        "deleted",
        "userupdate",
        "user",
        "inlinequery",
        "inline",
        "callbackquery",
        "callback",
        "chataction",
        "raw",
        "custom",
    }
    issues: list[LintIssue] = []
    if event_type.lower() not in valid:
        issues.append(
            LintIssue(
                node.lineno,
                f'unknown MCUB event type "{event_type}"',
                code="invalid-event-type",
                path=path,
            )
        )
    if event_type.lower() in {"callbackquery", "callback"} and not _keyword_bool(
        decorator, "bot_client"
    ):
        issues.append(
            LintIssue(
                node.lineno,
                "callback query events require bot_client=True",
                code="callbackquery-requires-bot-client",
                path=path,
            )
        )
    return issues


def _lint_watcher_decorator(
    path: Path, node: ast.AST, decorator: ast.expr
) -> list[LintIssue]:
    keywords = (
        {keyword.arg: keyword.value for keyword in decorator.keywords}
        if isinstance(decorator, ast.Call)
        else {}
    )
    issues: list[LintIssue] = []
    for positive, negative in (
        ("out", "incoming"),
        ("only_pm", "no_pm"),
        ("only_groups", "no_groups"),
        ("only_channels", "no_channels"),
        ("only_media", "no_media"),
        ("only_photos", "no_photos"),
        ("only_videos", "no_videos"),
        ("only_audios", "no_audios"),
        ("only_docs", "no_docs"),
        ("only_stickers", "no_stickers"),
        ("only_forwards", "no_forwards"),
        ("only_reply", "no_reply"),
    ):
        if _literal_true(keywords.get(positive)) and _literal_true(
            keywords.get(negative)
        ):
            issues.append(
                LintIssue(
                    node.lineno,
                    f"watcher filters {positive}=True and {negative}=True conflict",
                    code="conflicting-watcher-filters",
                    path=path,
                )
            )
    filter_keys = set(keywords) - {"bot_client"}
    if not filter_keys and any(
        isinstance(call, ast.Call) and _is_heavy_io_call(call)
        for call in ast.walk(node)
    ):
        issues.append(
            LintIssue(
                node.lineno,
                "unfiltered watcher performs potentially heavy I/O for every message",
                severity="warning",
                code="unfiltered-watcher",
                path=path,
            )
        )
    return issues


def _is_heavy_io_call(call: ast.Call) -> bool:
    name = _call_name(call.func) or ""
    if name.startswith(("requests.", "subprocess.", "urllib.request.")):
        return True
    if name in {"open", "run", "check_output", "system", "popen", "urlopen"}:
        return True
    parts = name.split(".")
    if (
        len(parts) >= 2
        and parts[-1] in {"get", "post", "request"}
        and parts[-2].lower() in {"session", "client", "http", "aiohttp"}
    ):
        return True
    return parts[-1] in {"read_text", "read_bytes", "write_text", "write_bytes"}


def _lint_loop_decorator(
    path: Path, node: ast.AST, decorator: ast.expr
) -> list[LintIssue]:
    if not isinstance(decorator, ast.Call):
        return []
    interval = (
        decorator.args[0]
        if decorator.args
        else next(
            (
                keyword.value
                for keyword in decorator.keywords
                if keyword.arg == "interval"
            ),
            None,
        )
    )
    if (
        isinstance(interval, ast.Constant)
        and isinstance(interval.value, (int, float))
        and interval.value <= 0
    ):
        return [
            LintIssue(
                node.lineno,
                "loop interval must be greater than zero",
                code="invalid-loop-interval",
                path=path,
            )
        ]
    return []


def _lint_manual_handlers_and_inline(path: Path, tree: ast.AST) -> list[LintIssue]:
    issues: list[LintIssue] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node.func) or ""
        if name.endswith(".add_event_handler"):
            issues.append(
                LintIssue(
                    node.lineno,
                    "manual event handlers are not automatically cleaned up by MCUB",
                    severity="warning",
                    code="manual-handler-registration",
                    path=path,
                )
            )
        if name.endswith(".inline_form"):
            issues.append(
                LintIssue(
                    node.lineno,
                    "kernel.inline_form is legacy; use kernel.inline.form",
                    severity="warning",
                    code="legacy-inline-form",
                    path=path,
                )
            )
        if name.endswith(".rich_form"):
            issues.extend(_lint_rich_media_ids(path, node))
    return issues


def _lint_rich_media_ids(path: Path, node: ast.Call) -> list[LintIssue]:
    if len(node.args) < 2:
        return []
    text = _literal_string(node.args[1])
    media = next(
        (keyword.value for keyword in node.keywords if keyword.arg == "rich_media"),
        None,
    )
    if text is None or not isinstance(media, ast.Dict):
        return []
    referenced = set(
        re.findall(
            r"tg://(?:media|photo|video|audio|document)\?id=([A-Za-z0-9_-]+)", text
        )
    )
    provided = {_literal_string(key) for key in media.keys if key is not None}
    return [
        LintIssue(
            node.lineno,
            f'rich media id "{key}" is absent from rich_media',
            code="rich-media-id-mismatch",
            path=path,
        )
        for key in sorted(referenced - provided)
    ]


def _keyword_bool(call: ast.Call, name: str) -> bool:
    return any(
        keyword.arg == name and _literal_true(keyword.value)
        for keyword in call.keywords
    )


def _literal_true(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value is True


def _find_string_accesses(
    path: Path, source: str
) -> list[tuple[Path, int, str, set[str]]]:
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
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and _is_self_strings(node.func.value)
        ):
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
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and _is_self_strings(node.func.value)
            and node.args
        ):
            argument = node.args[0]
        if argument is not None and _literal_string(argument) is None:
            dynamic.append((path, node.lineno))
    return dynamic


def _lint_locales(
    manifest: "Manifest",
    accesses: list[tuple[Path, int, str, set[str]]],
    dynamic_accesses: list[tuple[Path, int]],
) -> list[LintIssue]:
    issues = [
        LintIssue(
            line,
            "dynamic locale key cannot be validated statically",
            severity="warning",
            code="locale-dynamic-key",
            path=path,
        )
        for path, line in dynamic_accesses
    ]
    if not accesses and manifest.locales is None:
        return issues
    if manifest.locales is None:
        issues.extend(
            LintIssue(
                line,
                f'locale key "{key}" is used but no locales directory is configured',
                severity="warning",
                code="locale-key",
                path=path,
            )
            for path, line, key, _ in accesses
        )
        return issues
    locales = load_locales(manifest.locales)
    flat = {language: _flatten_locale(values) for language, values in locales.items()}
    used_keys = {key for _, _, key, _ in accesses}
    for path, line, key, provided in accesses:
        values = {
            language: entries[key]
            for language, entries in flat.items()
            if key in entries
        }
        missing = sorted(set(flat) - set(values))
        if not values:
            issues.append(
                LintIssue(
                    line,
                    f'locale key "{key}" does not exist',
                    code="locale-key",
                    path=path,
                )
            )
            continue
        if missing:
            issues.append(
                LintIssue(
                    line,
                    f'locale key "{key}" is missing in: {", ".join(missing)}',
                    code="locale-key",
                    path=path,
                )
            )
        expected = {frozenset(_format_placeholders(value)) for value in values.values()}
        if len(expected) > 1:
            issues.append(
                LintIssue(
                    line,
                    f'locale key "{key}" has inconsistent placeholders between locales',
                    code="locale-placeholder",
                    path=path,
                )
            )
        elif provided and expected:
            required = set(next(iter(expected)))
            missing_args = required - provided
            if missing_args:
                issues.append(
                    LintIssue(
                        line,
                        f'locale key "{key}" is missing placeholders: {", ".join(sorted(missing_args))}',
                        code="locale-placeholder",
                        path=path,
                    )
                )
    for language, entries in flat.items():
        locale_path = next(manifest.locales.glob(f"{language}.*"), manifest.locales)
        for key in sorted(set(entries) - used_keys):
            issues.append(
                LintIssue(
                    1,
                    f'locale key "{key}" is never used',
                    severity="warning",
                    code="locale-unused-key",
                    path=locale_path,
                )
            )
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
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "strings"
        and isinstance(node.value, ast.Name)
        and node.value.id == "self"
    )


def _literal_string(node: ast.expr) -> str | None:
    return (
        node.value
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        else None
    )


def _lint_hooks(manifest: "Manifest") -> list[LintIssue]:
    if manifest.hooks is None:
        return []
    issues: list[LintIssue] = []
    allowed_placeholders = {
        "project_dir",
        "manifest",
        "module_id",
        "command",
        "output",
        "profile",
    }
    hook_sets = [manifest.hooks.common, *manifest.hooks.profiles.values()]
    for events in hook_sets:
        for event, commands in events.items():
            for command in commands:
                for argument in command:
                    for placeholder in re.findall(r"\{([^{}]+)\}", argument):
                        if placeholder not in allowed_placeholders:
                            issues.append(
                                LintIssue(
                                    1,
                                    f'unknown hook placeholder "{placeholder}" in {event}',
                                    code="hook-placeholder",
                                    path=manifest.project_dir / "cubkit.toml",
                                )
                            )
                    if (
                        "{" in argument
                        or "}" in argument
                        or not _looks_like_project_path(argument)
                    ):
                        continue
                    if not (manifest.project_dir / argument).exists():
                        issues.append(
                            LintIssue(
                                1,
                                f"hook path does not exist: {argument}",
                                code="hook-path",
                                path=manifest.project_dir / "cubkit.toml",
                            )
                        )
    return issues


def _looks_like_project_path(argument: str) -> bool:
    return (
        "/" in argument
        or "\\" in argument
        or Path(argument).suffix in {".py", ".sh", ".bat"}
    )


def _rule_enabled(rule: str, config: "LintConfig") -> bool:
    return rule not in config.disable and (not config.enable or rule in config.enable)


def _apply_inline_ignores(
    issues: list[LintIssue], manifest: "Manifest"
) -> list[LintIssue]:
    filtered: list[LintIssue] = []
    source_cache: dict[Path, list[str]] = {}
    for issue in issues:
        if issue.path is None or not issue.path.is_file():
            filtered.append(issue)
            continue
        lines = source_cache.setdefault(
            issue.path, issue.path.read_text(encoding="utf-8").splitlines()
        )
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
    targets: tuple[Path, ...] | None = None,
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
        command = _tool_command(
            name, executable, targets or (manifest.source_root,), fix
        )
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


def _tool_command(
    name: str, executable: str, targets: tuple[Path, ...], fix: bool
) -> list[str]:
    paths = [str(path) for path in targets]
    if name == "ruff":
        return [executable, "check", *paths, *(["--fix"] if fix else [])]
    if name == "black":
        return (
            [executable, *paths] if fix else [executable, "--check", "--diff", *paths]
        )
    return [executable, *paths]


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
    aliases = _dependency_aliases(config.import_aliases)

    for module, line in _external_imports(tree):
        top_level = module.split(".", 1)[0]
        if (
            module in {"cubkit.lib", "cubkit.libs"}
            or module.startswith(("cubkit.lib.", "cubkit.libs."))
            or top_level in local_modules
            or _is_builtin(top_level, (*config.mcub_modules, *config.runtime_modules))
        ):
            continue
        distribution = (
            aliases.get(module)
            or aliases.get(top_level)
            or _distribution_for_import(top_level, distributions)
        )
        installed = _is_installed(top_level)
        declared_name = _normalize_distribution(distribution or top_level)
        is_declared = (
            declared_name in declared or _normalize_distribution(top_level) in declared
        )
        if not installed:
            issues.append(_import_issue(path, line, module, "is not installed", strict))
        elif not is_declared:
            issues.append(
                _import_issue(
                    path,
                    line,
                    module,
                    "is installed but absent from module.requires or libs",
                    strict,
                )
            )
    return issues


def _lint_unused_requires(
    manifest: "Manifest", used_imports: set[str]
) -> list[LintIssue]:
    aliases = _dependency_aliases(manifest.lint.import_aliases)
    used_distributions = {
        _normalize_distribution(
            aliases.get(
                import_name,
                aliases.get(import_name.split(".", 1)[0], import_name.split(".", 1)[0]),
            )
        )
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


def _dependency_aliases(custom: dict[str, str] | None) -> dict[str, str]:
    aliases = {
        "PIL": "Pillow",
        "cv2": "opencv-python",
        "sklearn": "scikit-learn",
        "bs4": "beautifulsoup4",
        "yaml": "PyYAML",
        "google.generativeai": "google-generativeai",
    }
    aliases.update(custom or {})
    return aliases


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
            issues.append(
                LintIssue(
                    1,
                    f"installed {requirement.name} {installed} does not satisfy {requirement.specifier}",
                    code="dependency-version-mismatch",
                    path=manifest.project_dir / "cubkit.toml",
                )
            )
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
    for async_function in (
        node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)
    ):
        for node in ast.walk(async_function):
            if not isinstance(node, ast.Call):
                continue
            name = _call_name(node.func)
            if name in blocked:
                issues.append(
                    LintIssue(
                        node.lineno,
                        blocked[name],
                        severity="warning",
                        code="blocking-io",
                        path=path,
                    )
                )
    return issues


def _lint_missing_await(
    path: Path, tree: ast.AST, aliases: dict[str, str] | None = None
) -> list[LintIssue]:
    async_names = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)
    }
    known_methods = {
        "respond",
        "reply",
        "edit",
        "delete",
        "answer",
        "send_message",
        "send_file",
        "download_media",
        "get_reply_message",
        "rich_form",
        "inline_form",
        "save_module_config",
        "get_module_config",
        "save_config",
        "disconnect",
        "connect",
    }
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    issues: list[LintIssue] = []
    calls = {
        node
        for function in ast.walk(tree)
        if isinstance(function, ast.AsyncFunctionDef)
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
    }
    for node in calls:
        name = _resolve_alias(_call_name(node.func), aliases or {})
        if (
            name not in async_names
            and (name or "").rsplit(".", 1)[-1] not in known_methods
            and not (name or "").endswith(".inline.form")
        ):
            continue
        parent = parents.get(node)
        handled = False
        while parent is not None and not isinstance(
            parent, (ast.stmt, ast.AsyncFunctionDef)
        ):
            if isinstance(parent, ast.Await) or (
                isinstance(parent, ast.Call)
                and (_call_name(parent.func) or "").rsplit(".", 1)[-1]
                in {"create_task", "ensure_future", "gather", "wait"}
            ):
                handled = True
                break
            parent = parents.get(parent)
        if not handled:
            issues.append(
                LintIssue(
                    node.lineno,
                    f'async call "{name}" is not awaited',
                    severity="warning",
                    code="missing-await",
                    path=path,
                )
            )
    return issues


def _lint_missing_cleanup(
    path: Path,
    tree: ast.AST,
    aliases: dict[str, str] | None = None,
    project_cleanup: set[str] | None = None,
    known_classes: set[str] | None = None,
) -> list[LintIssue]:
    issues: list[LintIssue] = []
    aliases = aliases or {}
    project_cleanup = project_cleanup or set()
    managed_calls = {
        item.context_expr
        for node in ast.walk(tree)
        if isinstance(node, (ast.With, ast.AsyncWith))
        for item in node.items
        if isinstance(item.context_expr, ast.Call)
    }
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    closed_targets = {
        name.removesuffix(".close")
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (name := _call_name(node.func)) is not None
        and name.endswith(".close")
    }
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        kind = (_resolve_alias(_call_name(call.func), aliases) or "").rsplit(".", 1)[-1]
        parent = parents.get(call)
        targets: list[ast.expr] = []
        if isinstance(parent, ast.Assign):
            targets = parent.targets
        elif isinstance(parent, ast.AnnAssign):
            targets = [parent.target]
        if kind == "ClientSession" and any(
            (_target_expression_name(target) or "") in closed_targets
            for target in targets
        ):
            managed_calls.add(call)

    def requires_cleanup(call: ast.AST) -> bool:
        if not isinstance(call, ast.Call):
            return False
        kind = (_resolve_alias(_call_name(call.func), aliases) or "").rsplit(".", 1)[-1]
        return kind == "create_task" or (
            kind == "ClientSession" and call not in managed_calls
        )

    for node in (
        item
        for item in ast.walk(tree)
        if isinstance(item, ast.ClassDef) and _is_module_class(item, known_classes)
    ):
        creates_resource = any(
            requires_cleanup(call)
            for method in node.body
            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
            for call in ast.walk(method)
        )
        has_cleanup = (
            node.name in project_cleanup
            or any(_expression_name(base) in project_cleanup for base in node.bases)
            or any(
                isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                and method.name == "on_unload"
                for method in node.body
            )
        )
        if creates_resource and not has_cleanup:
            issues.append(
                LintIssue(
                    node.lineno,
                    f'class "{node.name}" creates background tasks or sessions but has no on_unload()',
                    severity="warning",
                    code="missing-cleanup",
                    path=path,
                )
            )
    raw_resources = [node for node in ast.walk(tree) if requires_cleanup(node)]
    has_function_cleanup = any(
        isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            _expression_name(decorator) in {"uninstall", "on_uninstall"}
            for decorator in node.decorator_list
        )
        for node in ast.walk(tree)
    )
    has_module_class = any(
        isinstance(node, ast.ClassDef) and _is_module_class(node, known_classes)
        for node in ast.walk(tree)
    )
    if raw_resources and not has_module_class and not has_function_cleanup:
        issues.append(
            LintIssue(
                raw_resources[0].lineno,
                "module creates tasks or sessions but has no uninstall cleanup callback",
                severity="warning",
                code="missing-cleanup",
                path=path,
            )
        )
    return issues


def _target_expression_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _target_expression_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _lint_hardcoded_secrets(path: Path, tree: ast.AST) -> list[LintIssue]:
    patterns = (
        re.compile(r"^\d{7,12}:[A-Za-z0-9_-]{20,}$"),
        re.compile(r"-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
        re.compile(r"(?i)^(?:sk|api|token)[_-][A-Za-z0-9_-]{20,}$"),
    )
    return [
        LintIssue(
            node.lineno,
            "possible hardcoded credential in source",
            code="hardcoded-token",
            path=path,
        )
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and any(pattern.search(node.value) for pattern in patterns)
    ]


def _lint_assets(path: Path, tree: ast.AST, manifest: "Manifest") -> list[LintIssue]:
    issues: list[LintIssue] = []
    asset_methods = {"resource", "get_asset", "open_asset"}
    object_methods = {
        "assets.get",
        "assets.read_text",
        "assets.read_bytes",
        "assets.read_json",
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        name = _call_name(node.func) or ""
        if name.rsplit(".", 1)[-1] not in asset_methods and not any(
            name.endswith(f".{method}") or name == method for method in object_methods
        ):
            continue
        relative = _literal_string(node.args[0])
        if relative is None:
            continue
        target: Path | None = None
        if manifest.assets is not None:
            assets_root = manifest.assets.resolve()
            candidate = (assets_root / relative).resolve()
            try:
                candidate.relative_to(assets_root)
            except ValueError:
                issues.append(
                    LintIssue(
                        node.lineno,
                        f'asset path "{relative}" escapes the configured assets directory',
                        code="asset-missing",
                        path=path,
                    )
                )
                continue
            target = candidate
        if target is None or not target.is_file():
            issues.append(
                LintIssue(
                    node.lineno,
                    f'asset "{relative}" does not exist in the configured assets directory',
                    code="asset-missing",
                    path=path,
                )
            )
    return issues


def _lint_deprecated_mcub(path: Path, tree: ast.AST) -> list[LintIssue]:
    issues: list[LintIssue] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "register"
        ):
            arguments = [*node.args.posonlyargs, *node.args.args]
            if arguments and arguments[0].arg == "client":
                issues.append(
                    LintIssue(
                        node.lineno,
                        "register(client) is deprecated; MCUB now passes kernel",
                        severity="warning",
                        code="deprecated-register-client",
                        path=path,
                    )
                )
        if (
            isinstance(node, ast.Call)
            and _call_name(node.func) == "open"
            and node.args
            and _attribute_name(node.args[0]) == "kernel.CONFIG_FILE"
        ):
            issues.append(
                LintIssue(
                    node.lineno,
                    "direct access to kernel.CONFIG_FILE is deprecated",
                    severity="warning",
                    code="deprecated-config-file",
                    path=path,
                )
            )
        if (
            isinstance(node, ast.Call)
            and (_call_name(node.func) or "").rsplit(".", 1)[-1] == "ConfigValue"
            and len(node.args) >= 2
        ):
            validator = next(
                (
                    keyword.value
                    for keyword in node.keywords
                    if keyword.arg == "validator"
                ),
                None,
            )
            if isinstance(validator, ast.Call) and any(
                keyword.arg == "default" for keyword in validator.keywords
            ):
                issues.append(
                    LintIssue(
                        node.lineno,
                        "validator default duplicates the default already provided by ConfigValue",
                        severity="warning",
                        code="redundant-validator-default",
                        path=path,
                    )
                )
    return issues


def _lint_module_config(path: Path, tree: ast.AST) -> list[LintIssue]:
    config_calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (_call_name(node.func) or "").rsplit(".", 1)[-1] == "ModuleConfig"
    ]
    if not config_calls:
        return []
    issues: list[LintIssue] = []
    for config_call in config_calls:
        seen: dict[str, int] = {}
        for value in config_call.args:
            if (
                not isinstance(value, ast.Call)
                or (_call_name(value.func) or "").rsplit(".", 1)[-1] != "ConfigValue"
            ):
                continue
            key = _literal_string(value.args[0]) if value.args else None
            if key is not None:
                if key in seen:
                    issues.append(
                        LintIssue(
                            value.lineno,
                            f'duplicate ModuleConfig key "{key}" (first declared at line {seen[key]})',
                            code="duplicate-config-key",
                            path=path,
                        )
                    )
                else:
                    seen[key] = value.lineno
            issues.extend(_lint_config_default(path, value))
    module_classes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.ClassDef) and _is_module_class(node)
    ]
    if module_classes:
        for module_class in module_classes:
            on_load = next(
                (
                    node
                    for node in module_class.body
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and node.name == "on_load"
                ),
                None,
            )
            if on_load is not None and not _calls_super_on_load(on_load):
                issues.append(
                    LintIssue(
                        on_load.lineno,
                        "class-style on_load overrides ModuleBase config setup without awaiting super().on_load()",
                        code="class-config-super-missing",
                        path=path,
                    )
                )
    elif not any(
        isinstance(node, ast.Call)
        and (_call_name(node.func) or "").endswith("store_module_config_schema")
        for node in ast.walk(tree)
    ):
        issues.append(
            LintIssue(
                config_calls[0].lineno,
                "function-style ModuleConfig is not registered with store_module_config_schema()",
                severity="warning",
                code="module-config-schema-missing",
                path=path,
            )
        )
    return issues


def _lint_config_default(path: Path, value: ast.Call) -> list[LintIssue]:
    if len(value.args) < 2:
        return []
    default = _literal_value(value.args[1])
    validator = next(
        (keyword.value for keyword in value.keywords if keyword.arg == "validator"),
        None,
    )
    if default is _UNSET or not isinstance(validator, ast.Call):
        return []
    name = (_call_name(validator.func) or "").rsplit(".", 1)[-1]
    valid = True
    if name == "Boolean":
        valid = isinstance(default, bool)
    elif name == "Integer":
        valid = isinstance(default, int) and not isinstance(default, bool)
        minimum = _keyword_literal(validator, "min")
        maximum = _keyword_literal(validator, "max")
        if minimum is not _UNSET and not isinstance(minimum, (int, float)):
            minimum = _UNSET
        if maximum is not _UNSET and not isinstance(maximum, (int, float)):
            maximum = _UNSET
        valid = (
            valid
            and (minimum is _UNSET or default >= minimum)
            and (maximum is _UNSET or default <= maximum)
        )
    elif name == "Choice":
        choices_node = (
            validator.args[0]
            if validator.args
            else next(
                (
                    keyword.value
                    for keyword in validator.keywords
                    if keyword.arg == "choices"
                ),
                None,
            )
        )
        choices = _literal_value(choices_node) if choices_node is not None else _UNSET
        try:
            valid = choices is _UNSET or default in choices
        except TypeError:
            valid = True
    if valid:
        return []
    return [
        LintIssue(
            value.lineno,
            f"ConfigValue default is invalid for {name} validator",
            code="config-default-invalid",
            path=path,
        )
    ]


_UNSET = object()


def _literal_value(node: ast.expr) -> object:
    try:
        return ast.literal_eval(node)
    except (ValueError, TypeError):
        return _UNSET


def _keyword_literal(call: ast.Call, name: str) -> object:
    node = next(
        (keyword.value for keyword in call.keywords if keyword.arg == name), None
    )
    return _literal_value(node) if node is not None else _UNSET


def _calls_super_on_load(node: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    for item in ast.walk(node):
        if not isinstance(item, ast.Await) or not isinstance(item.value, ast.Call):
            continue
        call_name = _call_name(item.value.func)
        if call_name == "super.on_load" or call_name == "super().on_load":
            return True
        func = item.value.func
        if (
            isinstance(func, ast.Attribute)
            and func.attr == "on_load"
            and isinstance(func.value, ast.Call)
            and _call_name(func.value.func) == "super"
        ):
            return True
    return False


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


def _import_issue(
    path: Path, line: int, module: str, detail: str, strict: bool
) -> LintIssue:
    return LintIssue(
        line,
        f'import "{module}" {detail}',
        severity="error" if strict else "warning",
        code="external-import",
        path=path,
    )


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


def _distribution_for_import(
    name: str, distributions: dict[str, list[str]]
) -> str | None:
    packages = distributions.get(name)
    return packages[0] if packages else None


def _normalize_distribution(value: str) -> str:
    name = re.split(r"[<>=!~\[; ]", value, maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", name).lower()


def _is_module_class(node: ast.ClassDef, known_classes: set[str] | None = None) -> bool:
    return node.name in (known_classes or set()) or any(
        _expression_name(base) in {"ModuleBase", "Module"} for base in node.bases
    )


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


def _qualified_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Call):
        return _qualified_name(node.func)
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _resolve_alias(name: str | None, aliases: dict[str, str]) -> str | None:
    if name is None:
        return None
    root, separator, tail = name.partition(".")
    return aliases.get(root, root) + (separator + tail if separator else "")


def _project_cleanup_methods(files: tuple[SourceUnit, ...]) -> set[str]:
    classes: dict[str, tuple[set[str], bool]] = {}
    for unit in files:
        for node in ast.walk(unit.tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = {_expression_name(base) or "" for base in node.bases}
            has_cleanup = any(
                isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
                and method.name == "on_unload"
                for method in node.body
            )
            classes[node.name] = (bases, has_cleanup)
    cleanup = {name for name, (_, has_cleanup) in classes.items() if has_cleanup}
    changed = True
    while changed:
        changed = False
        for name, (bases, _) in classes.items():
            if name not in cleanup and bases.intersection(cleanup):
                cleanup.add(name)
                changed = True
    return cleanup


def _project_module_classes(files: tuple[SourceUnit, ...]) -> set[str]:
    classes: dict[str, set[str]] = {}
    for unit in files:
        for node in ast.walk(unit.tree):
            if isinstance(node, ast.ClassDef):
                classes[node.name] = {
                    _expression_name(base) or "" for base in node.bases
                }
    module_classes = {
        name
        for name, bases in classes.items()
        if bases.intersection({"ModuleBase", "Module"})
    }
    changed = True
    while changed:
        changed = False
        for name, bases in classes.items():
            if name not in module_classes and bases.intersection(module_classes):
                module_classes.add(name)
                changed = True
    return module_classes


def _lint_network_timeout(unit: SourceUnit) -> list[LintIssue]:
    issues: list[LintIssue] = []
    session_timeouts = _session_variables_with_default_timeout(unit)
    for node in ast.walk(unit.tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node.func) or ""
        method = name.rsplit(".", 1)[-1]
        if method not in {"get", "post", "put", "patch", "delete", "request"}:
            continue
        receiver = name.rsplit(".", 1)[0] if "." in name else ""
        receiver_type = unit.inferred_types.get(receiver, "")
        resolved = _resolve_alias(name, unit.aliases) or name
        is_network = (
            "ClientSession" in receiver_type
            or resolved.startswith(("aiohttp.", "requests."))
            or receiver.lower() in {"session", "http", "http_session"}
        )
        has_timeout = any(keyword.arg == "timeout" for keyword in node.keywords)
        if is_network and not has_timeout and receiver not in session_timeouts:
            issues.append(
                LintIssue(
                    node.lineno,
                    f'network call "{name}" has no explicit timeout',
                    severity="warning",
                    code="network-without-timeout",
                    path=unit.path,
                )
            )
    return issues


def _session_variables_with_default_timeout(unit: SourceUnit) -> set[str]:
    variables: set[str] = set()

    def is_timed_session(call: ast.expr) -> bool:
        return (
            isinstance(call, ast.Call)
            and (_resolve_alias(_call_name(call.func), unit.aliases) or "").rsplit(
                ".", 1
            )[-1]
            == "ClientSession"
            and any(keyword.arg == "timeout" for keyword in call.keywords)
        )

    for node in ast.walk(unit.tree):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                if item.optional_vars is not None and is_timed_session(
                    item.context_expr
                ):
                    name = _target_expression_name(item.optional_vars)
                    if name is not None:
                        variables.add(name)
        elif isinstance(node, ast.Assign) and is_timed_session(node.value):
            variables.update(
                name
                for target in node.targets
                if (name := _target_expression_name(target)) is not None
            )
        elif isinstance(node, ast.AnnAssign) and is_timed_session(node.value):
            name = _target_expression_name(node.target)
            if name is not None:
                variables.add(name)
    return variables


def _lint_session_per_handler(unit: SourceUnit) -> list[LintIssue]:
    issues: list[LintIssue] = []
    parents = {
        child: parent
        for parent in ast.walk(unit.tree)
        for child in ast.iter_child_nodes(parent)
    }
    handler_names = {"command", "bot_command", "watcher", "event", "callback", "inline"}
    for node in ast.walk(unit.tree):
        if not isinstance(node, ast.Call):
            continue
        resolved = _resolve_alias(_call_name(node.func), unit.aliases) or ""
        if resolved.rsplit(".", 1)[-1] != "ClientSession":
            continue
        parent = parents.get(node)
        while parent is not None and not isinstance(
            parent, (ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            parent = parents.get(parent)
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)) and any(
            _expression_name(decorator) in handler_names
            for decorator in parent.decorator_list
        ):
            issues.append(
                LintIssue(
                    node.lineno,
                    "ClientSession is created inside a message handler",
                    severity="warning",
                    code="session-created-per-request",
                    path=unit.path,
                )
            )
    return issues


def _lint_lost_task_reference(unit: SourceUnit) -> list[LintIssue]:
    parents = {
        child: parent
        for parent in ast.walk(unit.tree)
        for child in ast.iter_child_nodes(parent)
    }
    issues: list[LintIssue] = []
    for node in ast.walk(unit.tree):
        if not isinstance(node, ast.Call):
            continue
        resolved = _resolve_alias(_call_name(node.func), unit.aliases) or ""
        if resolved.rsplit(".", 1)[-1] != "create_task":
            continue
        if isinstance(parents.get(node), ast.Expr):
            issues.append(
                LintIssue(
                    node.lineno,
                    "background task reference is discarded",
                    severity="warning",
                    code="task-reference-lost",
                    path=unit.path,
                )
            )
    return issues


def _check_entrypoint(context: RuleContext) -> list[LintIssue]:
    unit = next(
        (unit for unit in context.files if unit.path == context.manifest.entrypoint),
        None,
    )
    return (
        lint_entrypoint(unit.path, _project_module_classes(context.files))
        if unit is not None and _is_active(context, unit)
        else []
    )


def _check_decorators(context: RuleContext) -> list[LintIssue]:
    classes = _project_module_classes(context.files)
    return [
        issue
        for unit in context.files
        if _is_active(context, unit)
        for issue in _lint_decorators(unit.path, unit.tree, context.manifest, classes)
    ]


def _check_async(context: RuleContext) -> list[LintIssue]:
    issues: list[LintIssue] = []
    class_cleanup = _project_cleanup_methods(context.files)
    module_classes = _project_module_classes(context.files)
    for unit in context.files:
        if not _is_active(context, unit):
            continue
        issues.extend(_lint_blocking_async_calls(unit.path, unit.tree))
        issues.extend(_lint_missing_await(unit.path, unit.tree, unit.aliases))
        issues.extend(
            _lint_missing_cleanup(
                unit.path, unit.tree, unit.aliases, class_cleanup, module_classes
            )
        )
        issues.extend(_lint_network_timeout(unit))
        issues.extend(_lint_session_per_handler(unit))
        issues.extend(_lint_lost_task_reference(unit))
    return issues


def _check_security(context: RuleContext) -> list[LintIssue]:
    return [
        issue
        for unit in context.files
        if _is_active(context, unit)
        for issue in _lint_hardcoded_secrets(unit.path, unit.tree)
    ]


def _check_assets(context: RuleContext) -> list[LintIssue]:
    return [
        issue
        for unit in context.files
        if _is_active(context, unit)
        for issue in _lint_assets(unit.path, unit.tree, context.manifest)
    ]


def _check_deprecated(context: RuleContext) -> list[LintIssue]:
    return [
        issue
        for unit in context.files
        if _is_active(context, unit)
        for issue in _lint_deprecated_mcub(unit.path, unit.tree)
    ]


def _check_config(context: RuleContext) -> list[LintIssue]:
    return [
        issue
        for unit in context.files
        if _is_active(context, unit)
        for issue in _lint_module_config(unit.path, unit.tree)
    ]


def _check_locales(context: RuleContext) -> list[LintIssue]:
    accesses = [
        item
        for unit in context.files
        for item in _find_string_accesses(unit.path, unit.source)
    ]
    dynamic = [
        item
        for unit in context.files
        for item in _find_dynamic_string_accesses(unit.path, unit.tree)
    ]
    return _lint_locales(context.manifest, accesses, dynamic)


def _check_hooks(context: RuleContext) -> list[LintIssue]:
    return _lint_hooks(context.manifest)


def _check_imports(context: RuleContext) -> list[LintIssue]:
    if not context.imports_enabled:
        return []
    local = _local_top_level_modules(context.manifest.source_root)
    return [
        issue
        for unit in context.files
        if _is_active(context, unit)
        for issue in _lint_imports(
            unit.path,
            unit.source,
            context.manifest.lint,
            context.manifest.requires,
            context.manifest.libs,
            local,
            context.imports_strict,
        )
    ]


def _check_dependencies(context: RuleContext) -> list[LintIssue]:
    used = {
        module for unit in context.files for module, _ in _external_imports(unit.tree)
    }
    return _lint_unused_requires(context.manifest, used) + _lint_dependency_versions(
        context.manifest
    )


def _check_tools(context: RuleContext) -> list[LintIssue]:
    return _lint_external_tools(
        context.manifest,
        context.progress,
        context.tool_output,
        context.fix,
        context.profile,
        (
            tuple(sorted(context.active_paths))
            if context.active_paths is not None
            else None
        ),
    )


_GROUP_CHECKS = {
    "entrypoint": _check_entrypoint,
    "decorators": _check_decorators,
    "async": _check_async,
    "security": _check_security,
    "assets": _check_assets,
    "deprecated": _check_deprecated,
    "config": _check_config,
    "locales": _check_locales,
    "hooks": _check_hooks,
    "imports": _check_imports,
    "dependencies": _check_dependencies,
    "tools": _check_tools,
}


_RULE_GROUPS = {
    "mcub-entrypoint": "entrypoint",
    "mcub-decorator": "decorators",
    "mcub-handler-signature": "decorators",
    "missing-handler-types": "decorators",
    "mcub-command": "decorators",
    "mcub-command-conflict": "decorators",
    "command-without-docs": "decorators",
    "inline-scope-missing": "decorators",
    "invalid-lifecycle-signature": "decorators",
    "invalid-event-type": "decorators",
    "callbackquery-requires-bot-client": "decorators",
    "conflicting-watcher-filters": "decorators",
    "unfiltered-watcher": "decorators",
    "invalid-loop-interval": "decorators",
    "manual-handler-registration": "decorators",
    "legacy-inline-form": "decorators",
    "rich-media-id-mismatch": "decorators",
    "blocking-io": "async",
    "missing-await": "async",
    "missing-cleanup": "async",
    "network-without-timeout": "async",
    "session-created-per-request": "async",
    "task-reference-lost": "async",
    "hardcoded-token": "security",
    "asset-missing": "assets",
    "deprecated-register-client": "deprecated",
    "deprecated-config-file": "deprecated",
    "redundant-validator-default": "deprecated",
    "duplicate-config-key": "config",
    "class-config-super-missing": "config",
    "module-config-schema-missing": "config",
    "config-default-invalid": "config",
    "locale-dynamic-key": "locales",
    "locale-key": "locales",
    "locale-placeholder": "locales",
    "locale-unused-key": "locales",
    "hook-placeholder": "hooks",
    "hook-path": "hooks",
    "external-import": "imports",
    "unused-require": "dependencies",
    "dependency-version-mismatch": "dependencies",
    "tool-missing": "tools",
    "tool-ruff": "tools",
    "tool-black": "tools",
    "tool-mypy": "tools",
}


def _make_rule_check(rule_id: str, group: str) -> RuleCheck:
    def check(context: RuleContext) -> list[LintIssue]:
        if group not in context.cache:
            context.cache[group] = _GROUP_CHECKS[group](context)
        return [issue for issue in context.cache[group] if issue.code == rule_id]

    check.__name__ = f"check_{rule_id.replace('-', '_')}"
    return check


def _is_active(context: RuleContext, unit: SourceUnit) -> bool:
    return context.active_paths is None or unit.path.resolve() in context.active_paths


RULES = tuple(
    LintRule(
        id=rule_id,
        level=(
            "warning"
            if rule_id
            in {
                "missing-handler-types",
                "command-without-docs",
                "inline-scope-missing",
                "unfiltered-watcher",
                "blocking-io",
                "missing-await",
                "missing-cleanup",
                "session-created-per-request",
                "task-reference-lost",
                "locale-dynamic-key",
                "locale-unused-key",
                "unused-require",
            }
            else "error"
        ),
        description=rule_id.replace("-", " ").capitalize(),
        fix=suggestion_for_rule(rule_id),
        group=group,
        check=_make_rule_check(rule_id, group),
    )
    for rule_id, group in _RULE_GROUPS.items()
)


def _annotation_names(node: ast.expr | None) -> set[str]:
    if node is None:
        return set()
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Attribute):
        return {node.attr}
    names: set[str] = set()
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.expr):
            names.update(_annotation_names(child))
    return names
