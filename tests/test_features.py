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
from cubkit.rules import RULES  # noqa: E402


class CubKitFeaturesTest(unittest.TestCase):
    def _project(self, root: Path, bundle: str = "") -> Path:
        project = root / "module"
        source = project / "src"
        source.mkdir(parents=True)
        (source / "main.py").write_text(
            "def register(kernel):\n    return kernel\n", encoding="utf-8"
        )
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
            project = self._project(
                Path(tmp),
                'debug_output = "dist/debug.py"\nrelease_output = "release/module.py"\n',
            )
            self.assertEqual(
                build_project(project, profile="debug"), project / "dist/debug.py"
            )
            self.assertEqual(
                build_project(project, profile="release"), project / "release/module.py"
            )

    def test_include_and_exclude_patterns_control_embedded_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(
                Path(tmp),
                'include = ["resources/**/*.json"]\nexclude = ["resources/private.json"]\n',
            )
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
            project = self._project(
                Path(tmp),
                'include = ["resources/config.txt"]\nfail_on_secrets = true\n',
            )
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
                'from pathlib import Path\nPath("common-ran").touch()\n',
                encoding="utf-8",
            )
            (scripts / "release.py").write_text(
                'from pathlib import Path\nPath("release-ran").touch()\n',
                encoding="utf-8",
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
            (scripts / "output.py").write_text(
                'print("hook output")\n', encoding="utf-8"
            )
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["build", str(project)]), 0)
            output = stderr.getvalue()
            self.assertIn("pre_build: python scripts/output.py", output)
            self.assertIn("hook output", output)
            self.assertGreater(
                output.index("hook output"), output.index("writing artifact")
            )

    def test_lint_reports_missing_mcub_entrypoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            (project / "src/main.py").write_text("VALUE = 1\n", encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["lint", str(project)]), 1)
            self.assertIn("main(), or register() function", stderr.getvalue())
            self.assertIn("Fix: Create a ModuleBase subclass", stderr.getvalue())
            self.assertIn(
                "Docs: https://github.com/hairpin01/CubKit/blob/main/doc/rules-lint.md#mcub-entrypoint",
                stderr.getvalue(),
            )
            self.assertIn(
                "MCUB: https://github.com/hairpin01/MCUB-fork/blob/main/doc/registration/class-style.md#quick-start",
                stderr.getvalue(),
            )

    def test_lint_accepts_modulebase_and_register_entrypoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            self.assertEqual(main(["lint", str(project)]), 0)
            (project / "src/main.py").write_text(
                "def main():\n    pass\n", encoding="utf-8"
            )
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
                self.assertEqual(
                    main(["lint", str(project), "--check-imports", "--strict"]), 1
                )

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
            project = self._project(
                Path(tmp), "[lint]\ncheck_imports = true\nstrict_imports = true\n"
            )
            (project / "src/main.py").write_text(
                "import cubkit.libs.telethon\nfrom cubkit.lib import aiohttp\n\ndef main():\n    pass\n",
                encoding="utf-8",
            )
            self.assertEqual(main(["lint", str(project)]), 0)

    def test_import_lint_allows_configured_runtime_modules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(
                Path(tmp),
                "[lint]\ncheck_imports = true\nstrict_imports = true\nruntime_modules = ["
                '"openagent_system_tool_api"]\n',
            )
            (project / "src/main.py").write_text(
                "import openagent_system_tool_api\n\ndef main():\n    pass\n",
                encoding="utf-8",
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
            lint_project(
                load_manifest(project),
                progress=lambda label, index, total: steps.append(
                    (label, index, total)
                ),
            )
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

    def test_lint_accepts_localized_command_documentation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            (project / "src/main.py").write_text(
                """class Demo(ModuleBase):
    @command("ping", doc_ru="Описание")
    async def ping(self, event):
        pass
""",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["lint", str(project)]), 0)
            self.assertNotIn("command-without-docs", stderr.getvalue())

    def test_lint_accepts_inline_message_for_callback_handler(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            (project / "src/main.py").write_text(
                """class Demo(ModuleBase):
    @callback
    async def handle(self, call: InlineMessage):
        pass
""",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["lint", str(project)]), 0)
            self.assertNotIn("missing-handler-types", stderr.getvalue())

    def test_lint_accepts_context_managed_client_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            (project / "src/main.py").write_text(
                """import aiohttp

async def main():
    async with aiohttp.ClientSession() as session:
        await session.get("https://example.com")
""",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["lint", str(project)]), 0)
            self.assertNotIn("missing-cleanup", stderr.getvalue())

    def test_cli_lint_reports_progress_and_checks_all_source_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            (project / "src/extra.py").write_text(
                'TOKEN = "123456789:abcdefghijklmnopqrstuvwxyzABCDE"\n',
                encoding="utf-8",
            )
            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["lint", str(project)]), 1)
            output = stderr.getvalue()
            self.assertIn("linting sources", output)
            self.assertIn("src/extra.py", output)
            self.assertIn("hardcoded-token", output)

    def test_rule_registry_has_metadata_and_unique_ids(self) -> None:
        self.assertEqual(len(RULES), len({rule.id for rule in RULES}))
        self.assertTrue(
            all(rule.level and rule.description and rule.fix for rule in RULES)
        )
        self.assertTrue(all(callable(rule.check) for rule in RULES))

    def test_lint_resolves_aliases_and_inferred_session_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            (project / "src/main.py").write_text(
                """import aiohttp as http

async def main():
    async with http.ClientSession() as client:
        await client.get("https://example.com")
""",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["lint", str(project)]), 0)
            self.assertIn("network-without-timeout", stderr.getvalue())
            self.assertNotIn("missing-cleanup", stderr.getvalue())

    def test_lint_accepts_client_session_default_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            (project / "src/main.py").write_text(
                """import aiohttp

async def main():
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        await session.get("https://example.com")
""",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["lint", str(project), "--no-cache"]), 0)
            self.assertNotIn("network-without-timeout", stderr.getvalue())

    def test_lint_accepts_try_finally_session_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            (project / "src/main.py").write_text(
                """from aiohttp import ClientSession as Session

async def main():
    session = Session()
    try:
        await session.get("https://example.com", timeout=10)
    finally:
        await session.close()
""",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["lint", str(project)]), 0)
            self.assertNotIn("missing-cleanup", stderr.getvalue())

    def test_lint_detects_per_handler_sessions_and_lost_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            (project / "src/main.py").write_text(
                """import asyncio as aio
from aiohttp import ClientSession as Session

class Demo(ModuleBase):
    @command("ping", doc_en="Ping")
    async def ping(self, event: Event):
        async with Session() as session:
            await session.get("https://example.com", timeout=10)
        aio.create_task(self.worker())

    async def worker(self):
        pass
""",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["lint", str(project)]), 0)
            output = stderr.getvalue()
            self.assertIn("session-created-per-request", output)
            self.assertIn("task-reference-lost", output)

    def test_lint_understands_cross_file_module_inheritance_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            (project / "src/base.py").write_text(
                """class Base(ModuleBase):
    async def on_unload(self):
        pass
""",
                encoding="utf-8",
            )
            (project / "src/main.py").write_text(
                """import asyncio
from .base import Base

class Demo(Base):
    async def start(self):
        self.task = asyncio.create_task(self.worker())

    async def worker(self):
        pass
""",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["lint", str(project)]), 0)
            self.assertNotIn("mcub-entrypoint", stderr.getvalue())
            self.assertNotIn("missing-cleanup", stderr.getvalue())

    def test_lint_cache_uses_source_and_rules_fingerprint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            manifest = load_manifest(project)
            cache = project / "lint-cache.json"
            with patch("cubkit.linter._cache_path", return_value=cache):
                lint_project(manifest, progress=lambda *_: None)
                progress: list[str] = []
                lint_project(
                    manifest,
                    progress=lambda label, *_: progress.append(label),
                )
                self.assertEqual(progress, ["lint cache hit"])

                (project / "src/main.py").write_text(
                    "def main():\n    return 1\n", encoding="utf-8"
                )
                progress.clear()
                lint_project(
                    load_manifest(project),
                    progress=lambda label, *_: progress.append(label),
                )
                self.assertNotEqual(progress, ["lint cache hit"])

    def test_cli_changed_lints_only_selected_python_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            extra = project / "src/extra.py"
            extra.write_text(
                'TOKEN = "123456789:abcdefghijklmnopqrstuvwxyzABCDE"\n',
                encoding="utf-8",
            )
            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch("cubkit.cli.git_changed_files", return_value={extra.resolve()}),
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                self.assertEqual(
                    main(["lint", str(project), "--changed", "--no-cache"]), 1
                )
            output = stderr.getvalue()
            self.assertIn("src/extra.py", output)
            self.assertNotIn("src/main.py", output)

    def test_github_init_generates_validation_release_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(main(["github", "init", str(project)]), 0)
            workflow = project / ".github/workflows/cubkit.yml"
            text = workflow.read_text(encoding="utf-8")
            self.assertIn("cubkit lint . --release --format sarif", text)
            self.assertIn("--release --reproducible", text)
            self.assertIn("github/codeql-action/upload-sarif@v3", text)
            self.assertIn("actions/upload-artifact@v4", text)

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["github", "init", str(project)]), 1)
            self.assertIn("already exists", stderr.getvalue())

    def test_lint_github_format_emits_annotations(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            (project / "src/main.py").write_text("VALUE = 1\n", encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(
                    main(
                        [
                            "lint",
                            str(project),
                            "--format",
                            "github",
                            "--no-cache",
                        ]
                    ),
                    1,
                )
            output = stdout.getvalue()
            self.assertIn(
                "::error file=src/main.py,line=1,title=mcub-entrypoint::", output
            )
            self.assertIn("Docs:", output)

    def test_lint_sarif_format_is_valid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            (project / "src/main.py").write_text("VALUE = 1\n", encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                self.assertEqual(
                    main(
                        [
                            "lint",
                            str(project),
                            "--format",
                            "sarif",
                            "--no-cache",
                        ]
                    ),
                    1,
                )
            sarif = json.loads(stdout.getvalue())
            self.assertEqual(sarif["version"], "2.1.0")
            result = sarif["runs"][0]["results"][0]
            self.assertEqual(result["ruleId"], "mcub-entrypoint")
            self.assertEqual(
                result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"],
                "src/main.py",
            )

    def test_lint_checks_locale_accesses_and_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            manifest = project / "cubkit.toml"
            manifest.write_text(
                manifest.read_text(encoding="utf-8").replace(
                    'entrypoint = "main.py"',
                    'entrypoint = "main.py"\nlocales = "locales"',
                ),
                encoding="utf-8",
            )
            locales = project / "locales"
            locales.mkdir()
            (locales / "en.yaml").write_text(
                "welcome: 'Hello {name}'\n", encoding="utf-8"
            )
            (locales / "ru.yaml").write_text(
                "welcome: 'Привет {username}'\n", encoding="utf-8"
            )
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
            with (
                patch("cubkit.rules.shutil.which", return_value=None),
                contextlib.redirect_stderr(stderr),
            ):
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
                    'id = "feature_mod"',
                    'id = "feature_mod"\nrequires = ["aiohttp", "unused-package"]',
                ),
                encoding="utf-8",
            )
            (project / "src/main.py").write_text(
                "import aiohttp\n\ndef main():\n    pass\n", encoding="utf-8"
            )
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

    def test_reproducible_build_is_byte_identical_and_has_manifest_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            first = build_project(project, reproducible=True)
            first_bytes = first.read_bytes()
            second = build_project(project, reproducible=True)
            self.assertEqual(first_bytes, second.read_bytes())
            self.assertIn(b"# CubKit reproducible build: true", first_bytes)
            self.assertIn(b"# CubKit manifest sha256: ", first_bytes)

    def test_lint_async_lifecycle_and_secret_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            (project / "src/main.py").write_text(
                """import asyncio

TOKEN = "123456789:abcdefghijklmnopqrstuvwxyzABCDE"

class Demo(ModuleBase):
    async def start(self, event):
        event.respond("hello")
        self.task = asyncio.create_task(self.worker())

    async def worker(self):
        pass
""",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["lint", str(project)]), 1)
            output = stderr.getvalue()
            self.assertIn("missing-await", output)
            self.assertIn("missing-cleanup", output)
            self.assertIn("hardcoded-token", output)

    def test_lint_command_docs_dynamic_locales_and_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            manifest = project / "cubkit.toml"
            manifest.write_text(
                manifest.read_text(encoding="utf-8").replace(
                    'entrypoint = "main.py"',
                    'entrypoint = "main.py"\nassets = "assets"\nlocales = "locales"',
                ),
                encoding="utf-8",
            )
            (project / "assets").mkdir()
            (project / "locales").mkdir()
            (project / "locales/en.yaml").write_text("unused: text\n", encoding="utf-8")
            (project / "src/main.py").write_text(
                """class Demo(ModuleBase):
    @command("ping")
    async def ping(self, event):
        self.strings(event.raw_text)
        resource("missing.txt")
""",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["lint", str(project)]), 1)
            output = stderr.getvalue()
            self.assertIn("command-without-docs", output)
            self.assertIn("locale-dynamic-key", output)
            self.assertIn("locale-unused-key", output)
            self.assertIn("asset-missing", output)

    def test_lint_dependency_version_and_deprecated_mcub_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            manifest = project / "cubkit.toml"
            manifest.write_text(
                manifest.read_text(encoding="utf-8").replace(
                    'id = "feature_mod"',
                    'id = "feature_mod"\nrequires = ["demo-package>=9"]',
                ),
                encoding="utf-8",
            )
            (project / "src/main.py").write_text(
                """def register(client):
    with open(kernel.CONFIG_FILE) as stream:
        pass
    return ConfigValue("enabled", False, validator=Boolean(default=False))
""",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with (
                patch("cubkit.rules.importlib.metadata.version", return_value="1.0"),
                contextlib.redirect_stderr(stderr),
            ):
                self.assertEqual(main(["lint", str(project)]), 1)
            output = stderr.getvalue()
            self.assertIn("dependency-version-mismatch", output)
            self.assertIn("deprecated-register-client", output)
            self.assertIn("deprecated-config-file", output)
            self.assertIn("redundant-validator-default", output)

    def test_lint_function_style_commands_and_lifecycle(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            (project / "src/main.py").write_text(
                """def register(kernel):
    @kernel.register.command("ping", doc_ru="Пинг")
    async def first(event):
        pass

    @kernel.register.command("ping", doc_en="Ping")
    async def second(event):
        pass

    @kernel.register.on_load()
    def setup(client):
        pass
""",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["lint", str(project)]), 1)
            output = stderr.getvalue()
            self.assertIn("mcub-command-conflict", output)
            self.assertIn("invalid-lifecycle-signature", output)
            self.assertIn("missing-handler-types", output)

    def test_lint_event_watcher_loop_and_inline_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            (project / "src/main.py").write_text(
                """def register(kernel):
    @kernel.register.event("callbackquery")
    async def callback(event):
        pass

    @kernel.register.event("unknown", bot_client=True)
    async def unknown(event):
        pass

    @kernel.register.watcher(only_pm=True, no_pm=True)
    async def watcher(event):
        pass

    @kernel.register.loop(interval=0)
    async def loop(kernel):
        pass

    kernel.client.add_event_handler(callback)
    kernel.inline_form(1, "text")
    kernel.rich_form(1, '<a href="tg://photo?id=hero">x</a>', rich_media={"other": object()})
""",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["lint", str(project)]), 1)
            output = stderr.getvalue()
            for code in (
                "callbackquery-requires-bot-client",
                "invalid-event-type",
                "conflicting-watcher-filters",
                "invalid-loop-interval",
                "manual-handler-registration",
                "legacy-inline-form",
                "rich-media-id-mismatch",
                "inline-scope-missing",
            ):
                self.assertIn(code, output)

    def test_lint_module_config_rules_and_class_super(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            (project / "src/main.py").write_text(
                """class Demo(ModuleBase):
    config = ModuleConfig(
        ConfigValue("enabled", "yes", validator=Boolean()),
        ConfigValue("enabled", False, validator=Boolean()),
    )

    async def on_load(self):
        pass
""",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["lint", str(project)]), 1)
            output = stderr.getvalue()
            self.assertIn("duplicate-config-key", output)
            self.assertIn("config-default-invalid", output)
            self.assertIn("class-config-super-missing", output)
            self.assertNotIn("module-config-schema-missing", output)

    def test_lint_accepts_class_config_with_super_on_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            (project / "src/main.py").write_text(
                """class Demo(ModuleBase):
    config = ModuleConfig(ConfigValue("enabled", False, validator=Boolean()))

    async def on_load(self):
        await super().on_load()
""",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["lint", str(project)]), 0)
            self.assertNotIn("class-config-super-missing", stderr.getvalue())

    def test_lint_uses_builtin_mcub_dependency_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            manifest = project / "cubkit.toml"
            manifest.write_text(
                manifest.read_text(encoding="utf-8").replace(
                    'id = "feature_mod"', 'id = "feature_mod"\nrequires = ["PyYAML"]'
                ),
                encoding="utf-8",
            )
            (project / "src/main.py").write_text(
                "import yaml\n\ndef main():\n    pass\n", encoding="utf-8"
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["lint", str(project)]), 0)
            self.assertNotIn("unused-require", stderr.getvalue())

    def test_lint_accepts_chataction_and_ignores_click_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            (project / "src/main.py").write_text(
                """def register(kernel):
    @kernel.register.event("chataction")
    async def action(event: Event):
        pass

@click.command()
def cli():
    pass
""",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["lint", str(project)]), 0)
            self.assertNotIn("invalid-event-type", stderr.getvalue())
            self.assertNotIn("mcub-command", stderr.getvalue())

    def test_lint_rejects_empty_command_and_wrong_event_type_annotation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            (project / "src/main.py").write_text(
                """def register(kernel):
    @kernel.register.command("", alias=["ping"], doc_en="Ping")
    async def ping(event: str):
        pass
""",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["lint", str(project)]), 1)
            self.assertIn("mcub-command", stderr.getvalue())
            self.assertIn("missing-handler-types", stderr.getvalue())

    def test_lint_handles_bad_integer_bounds_and_asset_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            manifest = project / "cubkit.toml"
            manifest.write_text(
                manifest.read_text(encoding="utf-8").replace(
                    'entrypoint = "main.py"',
                    'entrypoint = "main.py"\nassets = "assets"',
                ),
                encoding="utf-8",
            )
            (project / "assets").mkdir()
            (project / "src/main.py").write_text(
                """def main():
    config = ModuleConfig(ConfigValue("count", 1, validator=Integer(min="0")))
    resource("../../outside.txt")
""",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["lint", str(project)]), 1)
            self.assertIn("asset-missing", stderr.getvalue())

    def test_lint_inline_form_scope_and_watcher_io_precision(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            (project / "src/main.py").write_text(
                """def register(kernel):
    @kernel.register.watcher(out=True, incoming=True)
    async def watcher(event: Event):
        event.data.get("key")

    kernel.inline.form(1, "text")
""",
                encoding="utf-8",
            )
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["lint", str(project)]), 1)
            output = stderr.getvalue()
            self.assertIn("conflicting-watcher-filters", output)
            self.assertIn("inline-scope-missing", output)
            self.assertNotIn("unfiltered-watcher", output)

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
