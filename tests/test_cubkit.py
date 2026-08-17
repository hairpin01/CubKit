from pathlib import Path
import contextlib
import io
import importlib.util
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cubkit.builder import build_project, check_project
from cubkit.cli import main
from cubkit.lib_bundler import _github_requirement
from cubkit.types_sync import MCUB_TYPES_API_URL, sync_mcub_types


class _FakeResponse:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self) -> bytes:
        return self._data


def _write_test_wheel(
    path: Path,
    *,
    distribution: str,
    import_name: str,
    init_code: str,
    requires: list[str] | None = None,
) -> None:
    dist_info = f"{distribution.replace('-', '_')}-0.1.dist-info"
    metadata_lines = [
        "Metadata-Version: 2.1",
        f"Name: {distribution}",
        "Version: 0.1",
    ]
    for requirement in requires or []:
        metadata_lines.append(f"Requires-Dist: {requirement}")
    with zipfile.ZipFile(path, "w") as wheel:
        wheel.writestr(f"{import_name}/__init__.py", init_code)
        wheel.writestr(f"{dist_info}/METADATA", "\n".join(metadata_lines) + "\n")
        wheel.writestr(
            f"{dist_info}/WHEEL",
            "Wheel-Version: 1.0\nGenerator: cubkit-tests\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        wheel.writestr(f"{dist_info}/RECORD", "")


class CubKitTest(unittest.TestCase):
    def test_sync_mcub_types_downloads_files_and_gitignores_core(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "types_mod"
            project.mkdir()
            responses = {
                MCUB_TYPES_API_URL: b"""
                [
                  {"name": "event.py", "download_url": "https://example.invalid/event.py"},
                  {"name": "kernel.py", "download_url": "https://example.invalid/kernel.py"},
                  {"name": "README.md", "download_url": "https://example.invalid/README.md"}
                ]
                """,
                "https://example.invalid/event.py": b"class Event: pass\n",
                "https://example.invalid/kernel.py": b"class Kernel: pass\n",
            }

            def fake_urlopen(url: str) -> _FakeResponse:
                return _FakeResponse(responses[url])

            written = sync_mcub_types(project, urlopen=fake_urlopen)

            self.assertEqual([path.name for path in written], ["event.py", "kernel.py"])
            self.assertEqual(
                (project / "core" / "lib" / "types" / "event.py").read_text(encoding="utf-8"),
                "class Event: pass\n",
            )
            self.assertIn("core/", (project / ".gitignore").read_text(encoding="utf-8"))

    def test_cli_types_downloads_mcub_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "cli_types_mod"
            project.mkdir()
            responses = {
                MCUB_TYPES_API_URL: b'[{"name":"message.py","download_url":"https://example.invalid/message.py"}]',
                "https://example.invalid/message.py": b"class Message: pass\n",
            }

            def fake_urlopen(url: str) -> _FakeResponse:
                return _FakeResponse(responses[url])

            import cubkit.cli as cli_module

            original = cli_module.sync_mcub_types
            cli_module.sync_mcub_types = lambda path: sync_mcub_types(path, urlopen=fake_urlopen)
            stdout = io.StringIO()
            try:
                with contextlib.redirect_stdout(stdout):
                    self.assertEqual(main(["types", str(project)]), 0)
            finally:
                cli_module.sync_mcub_types = original

            self.assertIn("OK core/lib/types/message.py", stdout.getvalue())
            self.assertTrue((project / "core" / "lib" / "types" / "message.py").is_file())

    def test_package_github_requirement_forms(self) -> None:
        self.assertEqual(
            _github_requirement("git@github.com:hairpin01/tabfix.git"),
            "git+ssh://git@github.com/hairpin01/tabfix.git",
        )
        self.assertEqual(
            _github_requirement("ssh://git@github.com/hairpin01/tabfix.git"),
            "git+ssh://git@github.com/hairpin01/tabfix.git",
        )
        self.assertEqual(
            _github_requirement("https://github.com/hairpin01/tabfix"),
            "git+https://github.com/hairpin01/tabfix",
        )
        self.assertEqual(
            _github_requirement("hairpin01/tabfix"),
            "git+https://github.com/hairpin01/tabfix",
        )

    def test_init_check_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo_mod"
            self.assertEqual(main(["init", str(project)]), 0)
            self.assertEqual(check_project(project), 2)

            output = build_project(project)
            self.assertTrue(output.is_file())
            text = output.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("# name: demo_mod\n# version: 0.1.0\n"))
            self.assertIn("# description: Built with CubKit", text)
            self.assertIn("Generated by CubKit", text)
            self.assertIn("def cubkit_demo()", text)

    def test_manifest_out_sets_default_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "out_mod"
            project.mkdir()
            (project / "cubkit.toml").write_text(
                "\n".join(
                    [
                        'id = "out_mod"',
                        'entrypoint = "main.py"',
                        'out = "build/custom.py"',
                    ]
                ),
                encoding="utf-8",
            )
            (project / "main.py").write_text("VALUE = 1\n", encoding="utf-8")

            output = build_project(project)

            self.assertEqual(output, (project / "build" / "custom.py").resolve())
            self.assertTrue(output.is_file())

            override = project / "override.py"
            self.assertEqual(build_project(project, override), override.resolve())

    def test_cli_build_prints_progress(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "progress_mod"
            self.assertEqual(main(["init", str(project)]), 0)

            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["build", str(project)]), 0)

            progress = stderr.getvalue()
            self.assertIn("OK src/main.py", progress)
            self.assertIn("OK src/progress_mod_lib/__init__.py", progress)
            self.assertIn("OK src/progress_mod_lib/utils.py", progress)
            self.assertNotIn("\033", progress)
            self.assertIn("📐 Done! in ", stdout.getvalue())
            self.assertIn("Built dist/progress_mod.py", stdout.getvalue())

    def test_build_adds_manifest_metadata_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "meta_mod"
            project.mkdir()
            (project / "cubkit.toml").write_text(
                "\n".join(
                    [
                        'id = "meta_mod"',
                        'name = "MetaModule"',
                        'version = "1.2.3"',
                        'author = "@tester"',
                        'description = "Metadata demo"',
                        'requires = ["requests", "Pillow"]',
                        'banner_url = "https://example.invalid/banner.png"',
                        'scop = "kernel min v1.3.3"',
                        'entrypoint = "main.py"',
                    ]
                ),
                encoding="utf-8",
            )
            (project / "main.py").write_text("def register(kernel):\n    pass\n", encoding="utf-8")

            text = build_project(project).read_text(encoding="utf-8")

            self.assertTrue(
                text.startswith(
                    "# name: MetaModule\n"
                    "# version: 1.2.3\n"
                    "# author: @tester\n"
                    "# description: Metadata demo\n"
                    "# requires: requests, Pillow\n"
                    "# banner_url: https://example.invalid/banner.png\n"
                    "# scop: kernel min v1.3.3\n"
                )
            )

    def test_build_uses_class_style_name_for_metadata_header(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "class_mod"
            project.mkdir()
            (project / "cubkit.toml").write_text(
                "\n".join(
                    [
                        'id = "class_mod"',
                        'name = "ManifestName"',
                        'version = "1.0.0"',
                        'entrypoint = "main.py"',
                    ]
                ),
                encoding="utf-8",
            )
            (project / "main.py").write_text(
                "\n".join(
                    [
                        "from lib.utils import cmd_echo",
                        "",
                        "import core.lib.loader.module_base as loader",
                        "import core.lib.loader.module_config as cfg",
                        "",
                        "",
                        "class MyModules(loader.ModuleBase):",
                        "    name = 'MYmodule'",
                        "",
                        '    @loader.command("print")',
                        "    async def cmd(self, message) -> None:",
                        "        await self.cmd_echo(message)",
                    ]
                ),
                encoding="utf-8",
            )

            output = build_project(project)
            text = output.read_text(encoding="utf-8")

            self.assertEqual(output.name, "MYmodule.py")
            self.assertTrue(text.startswith("# name: MYmodule\n# version: 1.0.0\n"))
            self.assertNotIn("# name: ManifestName", text)
            self.assertIn("class MyModules(loader.ModuleBase):", text)

    def test_build_uses_manifest_name_for_class_style_without_name_attr(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "class_mod_without_name"
            project.mkdir()
            (project / "cubkit.toml").write_text(
                "\n".join(
                    [
                        'id = "class_mod_without_name"',
                        'name = "ManifestName"',
                        'version = "1.0.0"',
                        'entrypoint = "main.py"',
                    ]
                ),
                encoding="utf-8",
            )
            (project / "main.py").write_text(
                "\n".join(
                    [
                        "import core.lib.loader.module_base as loader",
                        "",
                        "class MyModules(loader.ModuleBase):",
                        "    pass",
                    ]
                ),
                encoding="utf-8",
            )

            output = build_project(project)
            text = output.read_text(encoding="utf-8")

            self.assertEqual(output.name, "class_mod_without_name.py")
            self.assertTrue(text.startswith("# name: ManifestName\n# version: 1.0.0\n"))

    def test_entrypoint_can_use_private_relative_imports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "relative_mod"
            project.mkdir()
            (project / "cubkit.toml").write_text(
                "\n".join(
                    [
                        'id = "relative_mod"',
                        'name = "RelativeMod"',
                        'version = "1.0.0"',
                        'entrypoint = "core.py"',
                    ]
                ),
                encoding="utf-8",
            )
            (project / "utils.py").write_text(
                "def helper():\n    return 'private-relative-ok'\n",
                encoding="utf-8",
            )
            (project / "core.py").write_text(
                "from .utils import helper\n\nVALUE = helper()\n",
                encoding="utf-8",
            )

            output = build_project(project)
            spec = importlib.util.spec_from_file_location("RelativeMod", output)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            module = importlib.util.module_from_spec(spec)
            sys.modules["RelativeMod"] = module
            try:
                spec.loader.exec_module(module)
                self.assertEqual(module.VALUE, "private-relative-ok")
            finally:
                sys.modules.pop("RelativeMod", None)

    def test_metadata_fields_are_optional_and_version_is_free_form(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "optional_meta"
            project.mkdir()
            (project / "cubkit.toml").write_text(
                "\n".join(
                    [
                        'id = "optional_meta"',
                        'version = "dev"',
                        'entrypoint = "main.py"',
                    ]
                ),
                encoding="utf-8",
            )
            (project / "main.py").write_text("VALUE = 1\n", encoding="utf-8")

            text = build_project(project).read_text(encoding="utf-8")

            self.assertTrue(text.startswith("# name: optional_meta\n# version: dev\n"))

            (project / "cubkit.toml").write_text(
                "\n".join(
                    [
                        'id = "optional_meta"',
                        'entrypoint = "main.py"',
                    ]
                ),
                encoding="utf-8",
            )
            text = build_project(project).read_text(encoding="utf-8")
            self.assertTrue(text.startswith("# name: optional_meta\n# CubKit build info:"))
            self.assertNotIn("# version:", text.split("# ---- CubKit entrypoint:", 1)[0])

    def test_future_imports_are_moved_before_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "future_mod"
            project.mkdir()
            (project / "cubkit.toml").write_text(
                "\n".join(
                    [
                        'id = "future_mod"',
                        'entrypoint = "main.py"',
                    ]
                ),
                encoding="utf-8",
            )
            (project / "main.py").write_text(
                '"""module doc"""\n\nfrom __future__ import annotations\n\nVALUE: list[str] = []\n',
                encoding="utf-8",
            )

            output = build_project(project)
            text = output.read_text(encoding="utf-8")

            self.assertIn("# name: future_mod\n# CubKit build info:", text)
            self.assertIn("from __future__ import annotations\n# Generated by CubKit", text)
            self.assertEqual(text.count("from __future__ import annotations"), 1)
            compile(text, str(output), "exec")

    def test_entrypoint_header_version_is_preserved_without_manifest_version(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "header_version_mod"
            project.mkdir()
            (project / "cubkit.toml").write_text(
                "\n".join(
                    [
                        'id = "header_version_mod"',
                        'entrypoint = "main.py"',
                    ]
                ),
                encoding="utf-8",
            )
            (project / "main.py").write_text(
                "# version: 0.8.0-main.build:1043\nVALUE = 1\n",
                encoding="utf-8",
            )

            text = build_project(project).read_text(encoding="utf-8")

            self.assertTrue(text.startswith("# name: header_version_mod\n# version: 0.8.0-main.build:1043\n"))

    def test_multiple_package_directories_are_supported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "multi_pkg_mod"
            project.mkdir()
            (project / "cubkit.toml").write_text(
                "\n".join(
                    [
                        'id = "multi_pkg_mod"',
                        'entrypoint = "main.py"',
                        'package = ["lib_one", "lib_two"]',
                    ]
                ),
                encoding="utf-8",
            )
            (project / "lib_one").mkdir()
            (project / "lib_two").mkdir()
            (project / "lib_one" / "alpha.py").write_text("VALUE_ALPHA = 'alpha'\n", encoding="utf-8")
            (project / "lib_two" / "beta.py").write_text("VALUE_BETA = 'beta'\n", encoding="utf-8")
            (project / "main.py").write_text(
                "from .alpha import VALUE_ALPHA\n"
                "from .beta import VALUE_BETA\n\n"
                "VALUE = VALUE_ALPHA + '-' + VALUE_BETA\n",
                encoding="utf-8",
            )

            output = build_project(project)
            text = output.read_text(encoding="utf-8")

            self.assertIn("__cubkit_package_dirs__ = ('lib_one', 'lib_two')", text)
            spec = importlib.util.spec_from_file_location("MultiPkgMod", output)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            module = importlib.util.module_from_spec(spec)
            sys.modules["MultiPkgMod"] = module
            try:
                spec.loader.exec_module(module)
                self.assertEqual(module.VALUE, "alpha-beta")
            finally:
                sys.modules.pop("MultiPkgMod", None)

    def test_sign_adds_signature_and_source_map_comments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "signed_mod"
            project.mkdir()
            (project / "cubkit.toml").write_text(
                "\n".join(
                    [
                        'id = "signed_mod"',
                        'entrypoint = "main.py"',
                        'package = "signed_lib"',
                        'sign = true',
                    ]
                ),
                encoding="utf-8",
            )
            (project / "signed_lib").mkdir()
            (project / "signed_lib" / "helper.py").write_text("HELPER = 'ok'\n", encoding="utf-8")
            (project / "main.py").write_text("from .helper import HELPER\nVALUE = HELPER\n", encoding="utf-8")

            text = build_project(project).read_text(encoding="utf-8")

            self.assertIn("# CubKit source sha256: ", text)
            self.assertIn("# CubKit payload sha256: ", text)
            self.assertIn("# CubKit signature: ", text)
            self.assertIn("# CubKit signature algorithm: sha256(cubkit-sign-v1", text)
            self.assertIn("# CubKit source map:", text)
            self.assertIn("-> main.py:1", text)
            self.assertIn("#   - signed_lib/helper.py -> helper.py:1", text)

    def test_src_root_places_code_under_src_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "src_root_mod"
            source_root = project / "src"
            source_root.mkdir(parents=True)
            (project / "cubkit.toml").write_text(
                "\n".join(
                    [
                        'id = "src_root_mod"',
                        'src = "src"',
                        'entrypoint = "main.py"',
                    ]
                ),
                encoding="utf-8",
            )
            (source_root / "mixin_command.py").write_text("COMMAND = 'cmd'\n", encoding="utf-8")
            (source_root / "mixin_callback.py").write_text("CALLBACK = 'callback'\n", encoding="utf-8")
            (source_root / "main.py").write_text(
                "from .mixin_command import COMMAND\n"
                "from .mixin_callback import CALLBACK\n\n"
                "VALUE = COMMAND + ':' + CALLBACK\n",
                encoding="utf-8",
            )

            output = build_project(project)
            text = output.read_text(encoding="utf-8")

            self.assertIn("# ---- CubKit entrypoint: main.py ----", text)
            self.assertIn("#   - mixin_callback.py -> mixin_callback.py:1", text)
            self.assertIn("#   - mixin_command.py -> mixin_command.py:1", text)

            spec = importlib.util.spec_from_file_location("SrcRootMod", output)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            module = importlib.util.module_from_spec(spec)
            sys.modules["SrcRootMod"] = module
            try:
                spec.loader.exec_module(module)
                self.assertEqual(module.VALUE, "cmd:callback")
            finally:
                sys.modules.pop("SrcRootMod", None)

    def test_local_libraries_are_importable_from_cubkit_lib(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "lib_mod"
            library_root = project / "vendor" / "genipng" / "src" / "genipng"
            library_root.mkdir(parents=True)
            project.mkdir(exist_ok=True)
            (project / "cubkit.toml").write_text(
                "\n".join(
                    [
                        'id = "lib_mod"',
                        'entrypoint = "main.py"',
                        '[libs.genipng]',
                        'type = "local"',
                        'path = "vendor/genipng"',
                    ]
                ),
                encoding="utf-8",
            )
            (library_root / "__init__.py").write_text(
                "def make_png():\n    return 'png-bytes'\n",
                encoding="utf-8",
            )
            (project / "main.py").write_text(
                "from cubkit.lib import genipng\n\nVALUE = genipng.make_png()\n",
                encoding="utf-8",
            )

            output = build_project(project)
            text = output.read_text(encoding="utf-8")

            self.assertIn("_cubkit_lib/genipng/__init__.py", text)
            self.assertIn("__cubkit_lib_dir__ = '_cubkit_lib'", text)

            spec = importlib.util.spec_from_file_location("LibMod", output)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            module = importlib.util.module_from_spec(spec)
            previous_lib = sys.modules.get("cubkit.lib")
            previous_genipng = sys.modules.get("cubkit.lib.genipng")
            sys.modules["LibMod"] = module
            try:
                spec.loader.exec_module(module)
                self.assertEqual(module.VALUE, "png-bytes")
            finally:
                sys.modules.pop("LibMod", None)
                if previous_genipng is None:
                    sys.modules.pop("cubkit.lib.genipng", None)
                else:
                    sys.modules["cubkit.lib.genipng"] = previous_genipng
                if previous_lib is None:
                    sys.modules.pop("cubkit.lib", None)
                    cubkit_pkg = sys.modules.get("cubkit")
                    if cubkit_pkg is not None and hasattr(cubkit_pkg, "lib"):
                        delattr(cubkit_pkg, "lib")
                else:
                    sys.modules["cubkit.lib"] = previous_lib

    def test_local_library_path_can_point_outside_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "outside_lib_mod"
            project.mkdir()
            library_root = root / "tabfix" / "src" / "tabfix"
            library_root.mkdir(parents=True)
            (project / "cubkit.toml").write_text(
                "\n".join(
                    [
                        'id = "outside_lib_mod"',
                        'entrypoint = "main.py"',
                        '[libs.tabfix]',
                        'type = "local"',
                        'path = "../tabfix"',
                    ]
                ),
                encoding="utf-8",
            )
            (library_root / "__init__.py").write_text(
                "def fix(value):\n    return value.upper()\n",
                encoding="utf-8",
            )
            (project / "main.py").write_text(
                "from cubkit.lib import tabfix\n\nVALUE = tabfix.fix('ok')\n",
                encoding="utf-8",
            )

            output = build_project(project)
            text = output.read_text(encoding="utf-8")

            self.assertIn("_cubkit_lib/tabfix/__init__.py", text)

            spec = importlib.util.spec_from_file_location("OutsideLibMod", output)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            module = importlib.util.module_from_spec(spec)
            previous_lib = sys.modules.get("cubkit.lib")
            previous_tabfix = sys.modules.get("cubkit.lib.tabfix")
            sys.modules["OutsideLibMod"] = module
            try:
                spec.loader.exec_module(module)
                self.assertEqual(module.VALUE, "OK")
            finally:
                sys.modules.pop("OutsideLibMod", None)
                if previous_tabfix is None:
                    sys.modules.pop("cubkit.lib.tabfix", None)
                else:
                    sys.modules["cubkit.lib.tabfix"] = previous_tabfix
                if previous_lib is None:
                    sys.modules.pop("cubkit.lib", None)
                    cubkit_pkg = sys.modules.get("cubkit")
                    if cubkit_pkg is not None and hasattr(cubkit_pkg, "lib"):
                        delattr(cubkit_pkg, "lib")
                else:
                    sys.modules["cubkit.lib"] = previous_lib

    def test_wheel_library_is_importable_from_cubkit_lib(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "wheel_lib_mod"
            project.mkdir()
            wheel_path = project / "genipng-0.1-py3-none-any.whl"
            with zipfile.ZipFile(wheel_path, "w") as wheel:
                wheel.writestr("genipng/__init__.py", "def make_png():\n    return 'wheel-png'\n")

            (project / "cubkit.toml").write_text(
                "\n".join(
                    [
                        'id = "wheel_lib_mod"',
                        'entrypoint = "main.py"',
                        '[libs.genipng]',
                        'type = "local"',
                        'path = "genipng-0.1-py3-none-any.whl"',
                    ]
                ),
                encoding="utf-8",
            )
            (project / "main.py").write_text(
                "from cubkit.lib import genipng\n\nVALUE = genipng.make_png()\n",
                encoding="utf-8",
            )

            output = build_project(project)
            text = output.read_text(encoding="utf-8")

            self.assertIn("_cubkit_lib/genipng/__init__.py", text)
            self.assertIn("genipng-0.1-py3-none-any.whl:genipng/__init__.py", text)

            spec = importlib.util.spec_from_file_location("WheelLibMod", output)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            module = importlib.util.module_from_spec(spec)
            previous_lib = sys.modules.get("cubkit.lib")
            previous_genipng = sys.modules.get("cubkit.lib.genipng")
            sys.modules["WheelLibMod"] = module
            try:
                spec.loader.exec_module(module)
                self.assertEqual(module.VALUE, "wheel-png")
            finally:
                sys.modules.pop("WheelLibMod", None)
                if previous_genipng is None:
                    sys.modules.pop("cubkit.lib.genipng", None)
                else:
                    sys.modules["cubkit.lib.genipng"] = previous_genipng
                if previous_lib is None:
                    sys.modules.pop("cubkit.lib", None)
                    cubkit_pkg = sys.modules.get("cubkit")
                    if cubkit_pkg is not None and hasattr(cubkit_pkg, "lib"):
                        delattr(cubkit_pkg, "lib")
                else:
                    sys.modules["cubkit.lib"] = previous_lib

    def test_native_binary_library_file_is_bundled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "native_lib_mod"
            native_dir = project / "native"
            native_dir.mkdir(parents=True)
            (native_dir / "fastpng.so").write_bytes(b"\x7fELF\x00\xffbinary-test")
            (project / "cubkit.toml").write_text(
                "\n".join(
                    [
                        'id = "native_lib_mod"',
                        'entrypoint = "main.py"',
                        '[libs.fastpng]',
                        'type = "local"',
                        'path = "native/fastpng.so"',
                    ]
                ),
                encoding="utf-8",
            )
            (project / "main.py").write_text("VALUE = 1\n", encoding="utf-8")

            text = build_project(project).read_text(encoding="utf-8")

            self.assertIn("_cubkit_lib/fastpng.so", text)
            self.assertIn("lines: binary", text)

    def test_cli_build_prints_collecting_dependencies_for_libs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "status_lib_mod"
            lib_root = project / "vendor" / "statuslib"
            lib_root.mkdir(parents=True)
            (lib_root / "__init__.py").write_text("VALUE = 'ok'\n", encoding="utf-8")
            (project / "cubkit.toml").write_text(
                "\n".join(
                    [
                        'id = "status_lib_mod"',
                        'entrypoint = "main.py"',
                        '[libs.statuslib]',
                        'type = "local"',
                        'path = "vendor/statuslib"',
                    ]
                ),
                encoding="utf-8",
            )
            (project / "main.py").write_text("VALUE = 1\n", encoding="utf-8")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                self.assertEqual(main(["build", str(project)]), 0)

            self.assertIn("collecting dependencies... statuslib", stderr.getvalue())
            self.assertIn("📐 Done! in ", stdout.getvalue())

    def test_package_pip_library_is_installed_and_vendored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "piplib_pkg"
            source = package / "piplib"
            source.mkdir(parents=True)
            (package / "setup.py").write_text(
                "from setuptools import setup\n"
                "setup(name='piplib', version='0.1', packages=['piplib'])\n",
                encoding="utf-8",
            )
            (source / "__init__.py").write_text("def value():\n    return 'pip-local'\n", encoding="utf-8")

            project = root / "pip_lib_mod"
            project.mkdir()
            (project / "cubkit.toml").write_text(
                "\n".join(
                    [
                        'id = "pip_lib_mod"',
                        'entrypoint = "main.py"',
                        '[libs.piplib]',
                        'package_pip = "' + package.as_posix() + '"',
                    ]
                ),
                encoding="utf-8",
            )
            (project / "main.py").write_text(
                "from cubkit.lib import piplib\n\nVALUE = piplib.value()\n",
                encoding="utf-8",
            )

            output = build_project(project)
            text = output.read_text(encoding="utf-8")
            self.assertIn("_cubkit_lib/piplib/__init__.py", text)

            spec = importlib.util.spec_from_file_location("PipLibMod", output)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            module = importlib.util.module_from_spec(spec)
            previous_lib = sys.modules.get("cubkit.lib")
            previous_piplib = sys.modules.get("cubkit.lib.piplib")
            sys.modules["PipLibMod"] = module
            try:
                spec.loader.exec_module(module)
                self.assertEqual(module.VALUE, "pip-local")
            finally:
                sys.modules.pop("PipLibMod", None)
                if previous_piplib is None:
                    sys.modules.pop("cubkit.lib.piplib", None)
                else:
                    sys.modules["cubkit.lib.piplib"] = previous_piplib
                if previous_lib is None:
                    sys.modules.pop("cubkit.lib", None)
                    cubkit_pkg = sys.modules.get("cubkit")
                    if cubkit_pkg is not None and hasattr(cubkit_pkg, "lib"):
                        delattr(cubkit_pkg, "lib")
                else:
                    sys.modules["cubkit.lib"] = previous_lib

    def test_local_installable_library_vendors_its_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            dependency = root / "deplib_pkg"
            dependency_source = dependency / "deplib"
            dependency_source.mkdir(parents=True)
            (dependency / "setup.py").write_text(
                "from setuptools import setup\n"
                "setup(name='deplib', version='0.1', packages=['deplib'])\n",
                encoding="utf-8",
            )
            (dependency_source / "__init__.py").write_text(
                "def suffix():\n    return 'dep'\n",
                encoding="utf-8",
            )

            library = root / "piplib_pkg"
            library_source = library / "piplib"
            library_source.mkdir(parents=True)
            (library / "setup.py").write_text(
                "from setuptools import setup\n"
                "setup(\n"
                "    name='piplib',\n"
                "    version='0.1',\n"
                "    packages=['piplib'],\n"
                f"    install_requires=['deplib @ file://{dependency.as_posix()}'],\n"
                ")\n",
                encoding="utf-8",
            )
            (library_source / "__init__.py").write_text(
                "import deplib\n\n"
                "def value():\n    return 'local-' + deplib.suffix()\n",
                encoding="utf-8",
            )

            project = root / "local_deps_mod"
            project.mkdir()
            (project / "cubkit.toml").write_text(
                "\n".join(
                    [
                        'id = "local_deps_mod"',
                        'entrypoint = "main.py"',
                        '[libs.piplib]',
                        'type = "local"',
                        'path = "../piplib_pkg"',
                    ]
                ),
                encoding="utf-8",
            )
            (project / "main.py").write_text(
                "from cubkit.lib import piplib\n\nVALUE = piplib.value()\n",
                encoding="utf-8",
            )

            output = build_project(project)
            text = output.read_text(encoding="utf-8")
            self.assertIn("_cubkit_lib/piplib/__init__.py", text)
            self.assertIn("_cubkit_lib/deplib/__init__.py", text)

            spec = importlib.util.spec_from_file_location("LocalDepsMod", output)
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            module = importlib.util.module_from_spec(spec)
            previous_modules = {
                name: sys.modules.get(name)
                for name in ("cubkit.lib", "cubkit.lib.piplib", "deplib")
            }
            sys.modules["LocalDepsMod"] = module
            try:
                spec.loader.exec_module(module)
                self.assertEqual(module.VALUE, "local-dep")
            finally:
                sys.modules.pop("LocalDepsMod", None)
                for name, previous in previous_modules.items():
                    if previous is None:
                        sys.modules.pop(name, None)
                    else:
                        sys.modules[name] = previous
                cubkit_pkg = sys.modules.get("cubkit")
                if previous_modules["cubkit.lib"] is None and cubkit_pkg is not None and hasattr(cubkit_pkg, "lib"):
                    delattr(cubkit_pkg, "lib")

    def test_package_github_accepts_pip_installable_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "gitlib_pkg"
            source = package / "gitlib"
            source.mkdir(parents=True)
            (package / "setup.py").write_text(
                "from setuptools import setup\n"
                "setup(name='gitlib', version='0.1', packages=['gitlib'])\n",
                encoding="utf-8",
            )
            (source / "__init__.py").write_text("VALUE = 'github-like-url'\n", encoding="utf-8")

            project = root / "github_lib_mod"
            project.mkdir()
            (project / "cubkit.toml").write_text(
                "\n".join(
                    [
                        'id = "github_lib_mod"',
                        'entrypoint = "main.py"',
                        '[libs.gitlib]',
                        'package_github = "file://' + package.as_posix() + '"',
                    ]
                ),
                encoding="utf-8",
            )
            (project / "main.py").write_text(
                "from cubkit.lib import gitlib\n\nVALUE = gitlib.VALUE\n",
                encoding="utf-8",
            )

            text = build_project(project).read_text(encoding="utf-8")

            self.assertIn("_cubkit_lib/gitlib/__init__.py", text)


if __name__ == "__main__":
    unittest.main()
