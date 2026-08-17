from __future__ import annotations

import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cubkit.builder import build_project, check_project  # noqa: E402
from cubkit.cli import main  # noqa: E402
from cubkit.collector import collect_bundle_files  # noqa: E402
from cubkit.errors import BuildError  # noqa: E402
from cubkit.manifest import load_manifest  # noqa: E402
from cubkit.linter import lint_project  # noqa: E402


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

    def test_hook_output_is_delayed_until_progress_finishes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(
                Path(tmp),
                """
[hooks]
pre_build = ["python", "scripts/output.py"]
""",
            )
            scripts = project / "scripts"
            scripts.mkdir()
            (scripts / "output.py").write_text('print("hook output")\n', encoding="utf-8")
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["build", str(project)]), 0)
            output = stderr.getvalue()
            self.assertIn("pre_build: python scripts/output.py", output)
            self.assertIn("hook output", output)
            self.assertGreater(output.index("hook output"), output.index("writing artifact"))

    def test_lint_reports_missing_mcub_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            (project / "src/main.py").write_text("VALUE = 1\n", encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["lint", str(project)]), 1)
            self.assertIn("main(), or register() function", stderr.getvalue())

    def test_lint_accepts_modulebase_and_register_entrypoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            self.assertEqual(main(["lint", str(project)]), 0)
            (project / "src/main.py").write_text("def main():\n    pass\n", encoding="utf-8")
            self.assertEqual(main(["lint", str(project)]), 0)
            (project / "src/main.py").write_text(
                "class Sample(ModuleBase):\n    pass\n", encoding="utf-8"
            )
            self.assertEqual(main(["lint", str(project)]), 0)

    def test_import_lint_warns_normally_and_fails_in_strict_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            (project / "src/main.py").write_text(
                "import definitely_missing_package\n\ndef main():\n    pass\n",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["lint", str(project), "--check-imports"]), 0)
            self.assertIn("WARNING", stderr.getvalue())
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(main(["lint", str(project), "--check-imports", "--strict"]), 1)

    def test_import_lint_allows_mcub_and_declared_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(
                Path(tmp),
                """
[lint]
check_imports = true

[lint.import_aliases]
yaml = "PyYAML"
""",
            )
            manifest = project / "cubkit.toml"
            manifest.write_text(
                manifest.read_text(encoding="utf-8").replace(
                    'id = "feature_mod"', 'id = "feature_mod"\nrequires = ["PyYAML"]'
                ),
                encoding="utf-8",
            )
            (project / "src/main.py").write_text(
                "import core\nimport yaml\n\ndef main():\n    pass\n",
                encoding="utf-8",
            )
            self.assertEqual(main(["lint", str(project)]), 0)

    def test_import_lint_allows_cubkit_vendored_libraries(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp), "[lint]\ncheck_imports = true\nstrict_imports = true\n")
            (project / "src/main.py").write_text(
                "import cubkit.libs.telethon\nfrom cubkit.lib import aiohttp\n\ndef main():\n    pass\n",
                encoding="utf-8",
            )
            self.assertEqual(main(["lint", str(project)]), 0)

    def test_import_lint_allows_configured_runtime_modules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(
                Path(tmp),
                "[lint]\ncheck_imports = true\nstrict_imports = true\nruntime_modules = ["\
                '"openagent_system_tool_api"]\n',
            )
            (project / "src/main.py").write_text(
                "import openagent_system_tool_api\n\ndef main():\n    pass\n", encoding="utf-8"
            )
            self.assertEqual(main(["lint", str(project)]), 0)

    def test_auto_lint_blocks_strict_import_errors_before_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(
                Path(tmp),
                """

[lint]
auto = true
check_imports = true
strict_imports = true
""",
            )
            (project / "src/main.py").write_text(
                "import definitely_missing_package\n\ndef main():\n    pass\n",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["build", str(project)]), 1)
            self.assertIn("external-import", stderr.getvalue())
            self.assertFalse((project / "dist/feature_mod.py").exists())

    def test_lint_reports_determinate_source_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            (project / "src/extra.py").write_text("VALUE = 1\n", encoding="utf-8")
            steps: list[tuple[str, int, int]] = []
            lint_project(load_manifest(project), progress=lambda label, index, total: steps.append((label, index, total)))
            self.assertEqual([step[1] for step in steps], [1, 2])
            self.assertTrue(all(step[2] == 2 for step in steps))
            self.assertTrue(all(step[0].startswith("linting src/") for step in steps))

    def test_lint_detects_invalid_handlers_and_command_conflicts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            (project / "src/main.py").write_text(
                """class Demo(ModuleBase):
    @command("ping")
    def first(self, event):
        pass

    @command("ping")
    async def second(self, event):
        pass
""",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["lint", str(project)]), 1)
            self.assertIn("mcub-decorator", stderr.getvalue())
            self.assertIn("mcub-command-conflict", stderr.getvalue())

    def test_lint_checks_locale_accesses_and_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            manifest = project / "cubkit.toml"
            manifest.write_text(
                manifest.read_text(encoding="utf-8").replace(
                    'entrypoint = "main.py"', 'entrypoint = "main.py"\nlocales = "locales"'
                ),
                encoding="utf-8",
            )
            locales = project / "locales"
            locales.mkdir()
            (locales / "en.yaml").write_text("welcome: 'Hello {name}'\n", encoding="utf-8")
            (locales / "ru.yaml").write_text("welcome: 'Привет {username}'\n", encoding="utf-8")
            (project / "src/main.py").write_text(
                """class Demo(ModuleBase):
    async def main(self, event):
        self.strings["unknown"]
        self.strings("welcome")
        self.strings.get("welcome")
""",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["lint", str(project)]), 1)
            self.assertIn("locale-key", stderr.getvalue())
            self.assertIn("locale-placeholder", stderr.getvalue())

    def test_lint_validates_hooks_and_honors_inline_ignore(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(
                Path(tmp),
                """
[lint]
check_imports = true
strict_imports = true

[hooks]
pre_build = ["python", "scripts/{unknown}.py"]
""",
            )
            (project / "src/main.py").write_text(
                "import definitely_missing_package  # cubkit: ignore[external-import]\n\ndef main():\n    pass\n",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["lint", str(project)]), 1)
            self.assertIn("hook-placeholder", stderr.getvalue())
            self.assertNotIn("external-import", stderr.getvalue())

    def test_lint_reports_missing_configured_external_tool(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(
                Path(tmp),
                "[lint]\nstrict_tools = true\n\n[lint.tools]\nruff = true\n",
            )
            stderr = io.StringIO()
            with patch("cubkit.linter.shutil.which", return_value=None), contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["lint", str(project)]), 1)
            self.assertIn("tool-missing", stderr.getvalue())

    def test_lint_tool_profiles_override_base_configuration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(
                Path(tmp),
                """[lint.tools]
ruff = true
black = true

[lint.tools.debug]
black = false

[lint.tools.release]
mypy = true
""",
            )
            tools = load_manifest(project).lint.tools
            self.assertTrue(tools.for_profile("debug").ruff)
            self.assertFalse(tools.for_profile("debug").black)
            self.assertTrue(tools.for_profile("release").black)
            self.assertTrue(tools.for_profile("release").mypy)

    def test_lint_reports_unused_requirements(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            manifest = project / "cubkit.toml"
            manifest.write_text(
                manifest.read_text(encoding="utf-8").replace(
                    'id = "feature_mod"', 'id = "feature_mod"\nrequires = ["aiohttp", "unused-package"]'
                ),
                encoding="utf-8",
            )
            (project / "src/main.py").write_text("import aiohttp\n\ndef main():\n    pass\n", encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["lint", str(project)]), 0)
            self.assertIn("unused-require", stderr.getvalue())

    def test_lint_warns_about_blocking_calls_in_async_handlers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            (project / "src/main.py").write_text(
                """import time
import asyncio

async def main():
    time.sleep(1)
    await asyncio.sleep(1)
""",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["lint", str(project)]), 0)
            self.assertIn("blocking-io", stderr.getvalue())

    def test_build_quiet_prints_only_relative_artifact_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["build", str(project), "--quiet"]), 0)
            self.assertEqual(stdout.getvalue(), "dist/feature_mod.py\n")
            self.assertEqual(stderr.getvalue(), "")

    def test_build_json_is_machine_readable_without_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["build", str(project), "--format", "json"]), 0)
            result = json.loads(stdout.getvalue())
            self.assertTrue(result["ok"])
            self.assertEqual(result["profile"], "default")
            self.assertEqual(stderr.getvalue(), "")
