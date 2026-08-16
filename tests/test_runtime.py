from __future__ import annotations

from contextlib import contextmanager
import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cubkit.builder import build_project, check_project  # noqa: E402
from cubkit.errors import ManifestError  # noqa: E402


@contextmanager
def _load_artifact(path: Path, module_name: str, cache_dir: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not create import spec for {path}")

    module = importlib.util.module_from_spec(spec)
    original_sys_path = sys.path[:]
    sys.modules[module_name] = module
    try:
        with patch.dict(os.environ, {"CUBKIT_CACHE_DIR": str(cache_dir)}):
            spec.loader.exec_module(module)
        yield module
    finally:
        sys.path[:] = original_sys_path
        sys.modules.pop(f"{module_name}._cubkit", None)
        sys.modules.pop(module_name, None)


class CubKitRuntimeTest(unittest.TestCase):
    def test_load_strings_returns_native_mcub_locale_mapping(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "runtime_mod"
            assets = project / "assets"
            locales = project / "locales"
            assets.mkdir(parents=True)
            locales.mkdir()

            (project / "cubkit.toml").write_text(
                "\n".join(
                    [
                        'id = "runtime_mod"',
                        'name = "Runtime Mod"',
                        'version = "1.2.3"',
                        'entrypoint = "main.py"',
                        'assets = "assets"',
                        'locales = "locales"',
                    ]
                ),
                encoding="utf-8",
            )
            (assets / "message.txt").write_text("asset-ok", encoding="utf-8")
            (locales / "en.json").write_text(
                '{"hello": "Hello, {name}!", "errors": {"missing": "Missing"}}',
                encoding="utf-8",
            )
            (locales / "ru.yaml").write_text(
                'hello: "Привет, {name}!"\nerrors:\n  missing: "Не найдено"\n',
                encoding="utf-8",
            )
            (project / "main.py").write_text(
                "\n".join(
                    [
                        "from cubkit import (",
                        "    assets, get_environment, load_strings, metadata, resource, root,",
                        ")",
                        "",
                        "class ModuleBase:",
                        "    pass",
                        "",
                        "class Mod(ModuleBase):",
                        "    strings = load_strings()",
                        "",
                        "class OtherMod(ModuleBase):",
                        "    strings = load_strings()",
                        "",
                        'ASSET_TEXT = assets.read_text("message.txt")',
                        'RESOURCE_NAME = resource("message.txt").name',
                        'MODULE_NAME = metadata["name"]',
                        'ROOT_MATCHES_ENV = root == get_environment()["root"]',
                        'Mod.strings["en"]["hello"] = "changed"',
                        'COPIES_ARE_ISOLATED = OtherMod.strings["en"]["hello"] != "changed"',
                    ]
                ),
                encoding="utf-8",
            )

            self.assertEqual(check_project(project), 3)
            output = build_project(project)
            with _load_artifact(output, "RuntimeMod", root / "cache") as module:
                self.assertEqual(module.ASSET_TEXT, "asset-ok")
                self.assertEqual(module.RESOURCE_NAME, "message.txt")
                self.assertEqual(module.MODULE_NAME, "Runtime Mod")
                self.assertTrue(module.ROOT_MATCHES_ENV)
                self.assertEqual(
                    module.OtherMod.strings,
                    {
                        "en": {
                            "hello": "Hello, {name}!",
                            "errors": {"missing": "Missing"},
                        },
                        "ru": {
                            "hello": "Привет, {name}!",
                            "errors": {"missing": "Не найдено"},
                        },
                    },
                )
                self.assertTrue(module.COPIES_ARE_ISOLATED)

    def test_load_strings_is_importable_without_configured_locales(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "minimal_mod"
            project.mkdir()
            (project / "cubkit.toml").write_text(
                'id = "minimal_mod"\nentrypoint = "main.py"\n',
                encoding="utf-8",
            )
            (project / "main.py").write_text(
                "from cubkit import load_strings\n\nSTRINGS = load_strings()\n",
                encoding="utf-8",
            )

            output = build_project(project)
            with _load_artifact(output, "MinimalMod", root / "cache") as module:
                self.assertEqual(module.STRINGS, {})

    def test_invalid_locale_value_fails_during_manifest_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "invalid_locale_mod"
            locales = project / "locales"
            locales.mkdir(parents=True)
            (project / "cubkit.toml").write_text(
                'id = "invalid_locale_mod"\nentrypoint = "main.py"\nlocales = "locales"\n',
                encoding="utf-8",
            )
            (project / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
            (locales / "en.yaml").write_text("not_a_string: 42\n", encoding="utf-8")

            with self.assertRaisesRegex(ManifestError, "must be a string"):
                check_project(project)

    def test_locale_filename_must_be_plain_lowercase_language_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "invalid_locale_name"
            locales = project / "locales"
            locales.mkdir(parents=True)
            (project / "cubkit.toml").write_text(
                'id = "invalid_locale_name"\nentrypoint = "main.py"\nlocales = "locales"\n',
                encoding="utf-8",
            )
            (project / "main.py").write_text("VALUE = 1\n", encoding="utf-8")
            (locales / "ru-RU.yaml").write_text('hello: "Привет"\n', encoding="utf-8")

            with self.assertRaisesRegex(ManifestError, "like en, ru or uk"):
                check_project(project)


if __name__ == "__main__":
    unittest.main()
