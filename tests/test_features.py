from __future__ import annotations

import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cubkit.builder import build_project, check_project  # noqa: E402
from cubkit.cli import main  # noqa: E402
from cubkit.collector import collect_bundle_files  # noqa: E402
from cubkit.errors import BuildError  # noqa: E402
from cubkit.manifest import load_manifest  # noqa: E402


class CubKitFeaturesTest(unittest.TestCase):
    def _project(self, root: Path, bundle: str = "") -> Path:
        project = root / "module"
        source = project / "src"
        source.mkdir(parents=True)
        (source / "main.py").write_text("def register(kernel):\n    return kernel\n", encoding="utf-8")
        (project / "cubkit.toml").write_text(
            """format = 2

[module]
id = "feature_mod"

[bundle]
source = "src"
entrypoint = "main.py"
""" + bundle,
            encoding="utf-8",
        )
        return project

    def test_profile_selects_configured_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp), 'debug_output = "dist/debug.py"\nrelease_output = "release/module.py"\n')
            self.assertEqual(build_project(project, profile="debug"), project / "dist/debug.py")
            self.assertEqual(build_project(project, profile="release"), project / "release/module.py")

    def test_include_and_exclude_patterns_control_embedded_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp), 'include = ["resources/**/*.json"]\nexclude = ["resources/private.json"]\n')
            resources = project / "resources"
            resources.mkdir()
            (resources / "public.json").write_text("{}", encoding="utf-8")
            (resources / "private.json").write_text("{}", encoding="utf-8")
            files = collect_bundle_files(load_manifest(project))
            names = {file.archive_name for file in files}
            self.assertIn("resources/public.json", names)
            self.assertNotIn("resources/private.json", names)

    def test_secret_check_rejects_suspicious_token(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp), 'include = ["resources/config.txt"]\nfail_on_secrets = true\n')
            (project / "resources").mkdir()
            (project / "resources/config.txt").write_text(
                'api_key = "abcdefghijklmnopqrstuvwxyz123456"\n', encoding="utf-8"
            )
            with self.assertRaisesRegex(BuildError, "possible secret"):
                check_project(project)

    def test_profile_hooks_run_only_for_selected_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(
                Path(tmp),
                """
[hooks]
pre_build = ["python", "scripts/common.py"]

[hooks.release]
post_build = ["python", "scripts/release.py"]
""",
            )
            scripts = project / "scripts"
            scripts.mkdir()
            (scripts / "common.py").write_text(
                'from pathlib import Path\nPath("common-ran").touch()\n', encoding="utf-8"
            )
            (scripts / "release.py").write_text(
                'from pathlib import Path\nPath("release-ran").touch()\n', encoding="utf-8"
            )
            build_project(project, profile="debug")
            self.assertTrue((project / "common-ran").is_file())
            self.assertFalse((project / "release-ran").exists())
            build_project(project, profile="release")
            self.assertTrue((project / "release-ran").is_file())

    def test_lint_reports_missing_mcub_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            (project / "src/main.py").write_text("VALUE = 1\n", encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["lint", str(project)]), 1)
            self.assertIn("register() function", stderr.getvalue())

    def test_lint_accepts_modulebase_and_register_entrypoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            self.assertEqual(main(["lint", str(project)]), 0)
            (project / "src/main.py").write_text(
                "class Sample(ModuleBase):\n    pass\n", encoding="utf-8"
            )
            self.assertEqual(main(["lint", str(project)]), 0)
