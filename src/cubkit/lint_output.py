"""Machine-readable output formats for CubKit lint diagnostics."""

from __future__ import annotations

from pathlib import Path

from .lint_docs import documentation_for_rule
from .rules import LintIssue, RULES


def render_github_annotations(issues: list[LintIssue], project_dir: Path) -> str:
    """Render GitHub workflow command annotations."""

    lines: list[str] = []
    for issue in issues:
        level = "error" if issue.severity == "error" else "warning"
        path = _relative_path(issue.path, project_dir)
        properties = f"file={_escape_property(path)},line={issue.line},title={_escape_property(issue.code)}"
        message = f"{issue.message} Fix: {issue.suggestion} Docs: {issue.docs_url}"
        lines.append(f"::{level} {properties}::{_escape_message(message)}")
    return "\n".join(lines)


def render_sarif(issues: list[LintIssue], project_dir: Path) -> dict[str, object]:
    """Return a SARIF 2.1.0 document suitable for GitHub Code Scanning."""

    rules = []
    for rule in RULES:
        docs_url, _ = documentation_for_rule(rule.id)
        rules.append(
            {
                "id": rule.id,
                "name": rule.id,
                "shortDescription": {"text": rule.description},
                "help": {"text": rule.fix},
                "helpUri": docs_url,
                "defaultConfiguration": {"level": _sarif_level(rule.level)},
            }
        )
    results = [
        {
            "ruleId": issue.code,
            "level": _sarif_level(issue.severity),
            "message": {"text": f"{issue.message} Fix: {issue.suggestion}"},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {
                            "uri": _relative_path(issue.path, project_dir)
                        },
                        "region": {"startLine": max(issue.line, 1)},
                    }
                }
            ],
        }
        for issue in issues
    ]
    return {
        "$schema": "https://json.schemastore.org/sarif-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "CubKit",
                        "informationUri": "https://github.com/hairpin01/CubKit",
                        "rules": rules,
                    }
                },
                "results": results,
            }
        ],
    }


def _relative_path(path: Path | None, project_dir: Path) -> str:
    if path is None:
        return "cubkit.toml"
    try:
        return path.resolve().relative_to(project_dir.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def _sarif_level(level: str) -> str:
    return "error" if level == "error" else "warning" if level == "warning" else "note"


def _escape_message(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_property(value: str) -> str:
    return _escape_message(value).replace(":", "%3A").replace(",", "%2C")
