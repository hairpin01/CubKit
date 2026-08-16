from __future__ import annotations

import contextlib
import io
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cubkit.builder import build_project  # noqa: E402
from cubkit.cli import main  # noqa: E402
from cubkit.errors import ManifestError  # noqa: E402
from cubkit.manifest import load_manifest  # noqa: E402


class ManifestV2Test(unittest.TestCase):
    def test_format_2_builds_with_structured_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "structured"
            source = project / "src"
            source.mkdir(parents=True)
            (source / "main.py").write_text("VALUE = 42\n", encoding="utf-8")
            (project / "cubkit.toml").write_text(
                """format = 2

[module]
id = "structured_mod"
name = "Structured Mod"
version = "2.0.0"
requires = ["aiohttp"]
scope = "inline"

[bundle]
source = "src"
entrypoint = "main.py"
output = "build/structured.py"
sign = true
""",
                encoding="utf-8",
            )

            manifest = load_manifest(project)
            self.assertEqual(manifest.format_version, 2)
            self.assertEqual(manifest.module_id, "structured_mod")
            self.assertEqual(manifest.requires, ("aiohttp",))
            self.assertEqual(manifest.scop, "inline")
            self.assertEqual(build_project(project), project / "build/structured.py")

    def test_legacy_manifest_remains_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
            (project / "cubkit.toml").write_text(
                'id = "legacy_mod"\nentrypoint = "main.py"\n', encoding="utf-8"
            )
            self.assertEqual(load_manifest(project).format_version, 1)

    def test_format_2_rejects_mixed_legacy_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
            (project / "cubkit.toml").write_text(
                """format = 2
id = "legacy_at_root"
[module]
id = "mixed_mod"
[bundle]
entrypoint = "main.py"
""",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(ManifestError, "cannot mix legacy"):
                load_manifest(project)

    def test_cli_migrate_creates_backup_and_valid_format_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "migrated"
            project.mkdir()
            original = (
                'id = "migrated_mod"\nname = "Migrated"\nentrypoint = "main.py"\n'
                'out = "dist/result.py"\nsign = true\n'
            )
            (project / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
            (project / "cubkit.toml").write_text(original, encoding="utf-8")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(["migrate", str(project)]), 0)

            self.assertIn("migrated:", output.getvalue())
            self.assertEqual(
                (project / "cubkit.toml.bak").read_text(encoding="utf-8"), original
            )
            raw = tomllib.loads((project / "cubkit.toml").read_text(encoding="utf-8"))
            self.assertEqual(raw["format"], 2)
            self.assertEqual(raw["module"]["id"], "migrated_mod")
            self.assertEqual(raw["bundle"]["output"], "dist/result.py")
            self.assertEqual(load_manifest(project).format_version, 2)
            self.assertTrue(build_project(project).is_file())

    def test_init_creates_format_2_src_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "new_mod"
            self.assertEqual(main(["init", str(project)]), 0)
            raw = tomllib.loads((project / "cubkit.toml").read_text(encoding="utf-8"))
            self.assertEqual(raw["format"], 2)
            self.assertEqual(raw["bundle"]["source"], "src")
            self.assertTrue((project / "src" / "main.py").is_file())
            self.assertEqual(load_manifest(project).format_version, 2)


if __name__ == "__main__":
    unittest.main()
