"""GitHub Actions workflow generation for CubKit projects."""

from __future__ import annotations

from pathlib import Path

from .errors import CubKitError
from .manifest import load_manifest


def initialize_github_actions(project_dir: Path, *, force: bool = False) -> Path:
    """Create the standard CubKit validation and release workflow."""

    project_dir = project_dir.resolve()
    load_manifest(project_dir)
    output = project_dir / ".github" / "workflows" / "cubkit.yml"
    if output.exists() and not force:
        raise CubKitError(f"GitHub workflow already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_WORKFLOW, encoding="utf-8")
    return output


_WORKFLOW = """name: CubKit

on:
  push:
  pull_request:

permissions:
  contents: read
  security-events: write

jobs:
  validate-and-build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
          cache: pip

      - name: Install CubKit
        run: python -m pip install --upgrade cubkit ruff black mypy

      - name: Lint module
        id: lint
        continue-on-error: true
        run: cubkit lint . --release --format sarif --no-cache > cubkit.sarif

      - name: Upload SARIF
        if: always() && hashFiles('cubkit.sarif') != ''
        uses: github/codeql-action/upload-sarif@v3
        with:
          sarif_file: cubkit.sarif

      - name: Build reproducible release twice
        if: steps.lint.outcome == 'success'
        run: |
          cubkit build . --release --reproducible --quiet -o dist/module.py
          cp dist/module.py /tmp/cubkit-first.py
          cubkit build . --release --reproducible --quiet -o dist/module.py
          cmp /tmp/cubkit-first.py dist/module.py

      - name: Upload release artifact
        if: steps.lint.outcome == 'success'
        uses: actions/upload-artifact@v4
        with:
          name: mcub-module
          path: dist/module.py
          if-no-files-found: error

      - name: Fail on lint errors
        if: steps.lint.outcome == 'failure'
        run: exit 1
"""
