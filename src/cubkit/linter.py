"""AST-based checks for common MCUB module entrypoint mistakes."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LintIssue:
    line: int
    message: str


def lint_entrypoint(path: Path) -> list[LintIssue]:
    """Return warnings for a module that MCUB is unlikely to load."""

    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    module_classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and _is_module_class(node)]
    register_functions = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "register"
    ]
    issues: list[LintIssue] = []
    if not module_classes and not register_functions:
        issues.append(LintIssue(1, "no ModuleBase/Module subclass or register() function found"))
    if len(module_classes) > 1:
        issues.append(LintIssue(module_classes[1].lineno, "multiple MCUB module classes found"))
    for module_class in module_classes:
        commands: set[str] = set()
        for method in module_class.body:
            if not isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if _has_decorator(method, "command"):
                if not isinstance(method, ast.AsyncFunctionDef):
                    issues.append(LintIssue(method.lineno, "@command handler should be async"))
                if method.name in commands:
                    issues.append(LintIssue(method.lineno, f"duplicate command handler: {method.name}"))
                commands.add(method.name)
    return issues


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
