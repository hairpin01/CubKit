"""Documentation links attached to CubKit lint diagnostics."""

from __future__ import annotations

CUBKIT_RULES_URL = "https://github.com/hairpin01/CubKit/blob/main/doc/rules-lint.md"
MCUB_DOCS_BASE = "https://github.com/hairpin01/MCUB-fork/blob/main/doc"

_MCUB_RULES = {
    "mcub-entrypoint": "registration/class-style.md#quick-start",
    "mcub-decorator": "registration/class-style.md#decorators",
    "mcub-handler-signature": "registration/class-style.md#decorators",
    "mcub-command": "api/commands.md#class-style-decorators",
    "mcub-command-conflict": "api/commands.md#registration-with-aliases",
    "locale-key": "guides/i18n.md#usage",
    "locale-placeholder": "guides/i18n.md#usage",
    "locale-unused-key": "guides/i18n.md#best-practices",
    "locale-dynamic-key": "guides/i18n.md#validation",
    "missing-cleanup": "registration/lifecycle.md#cleanup-order-on-unload",
    "command-without-docs": "api/commands.md#command-documentation",
    "deprecated-register-client": "guides/module-structure.md",
    "deprecated-config-file": "api/module-config.md#simple-dict-api",
    "redundant-validator-default": "api/module-config.md#available-validators",
    "callbackquery-requires-bot-client": "registration/enhanced-api.md#kernelregistereventevent_type-args-bot_clientfalse-kwargs",
    "invalid-event-type": "registration/enhanced-api.md#kernelregistereventevent_type-args-bot_clientfalse-kwargs",
    "conflicting-watcher-filters": "registration/watchers.md#available-tags",
    "unfiltered-watcher": "registration/watchers.md#syntax",
    "invalid-lifecycle-signature": "registration/lifecycle.md",
    "legacy-inline-form": "inline/inline-form.md",
    "module-config-schema-missing": "api/module-config.md#simple-dict-api",
    "class-config-super-missing": "registration/class-style.md#lifecycle-methods",
    "config-default-invalid": "api/module-config.md#available-validators",
    "duplicate-config-key": "api/module-config.md#moduleconfig-recommended",
    "missing-handler-types": "guides/best-practices.md#type-annotations",
    "inline-scope-missing": "guides/module-structure.md#kernel-compatibility-scop",
    "invalid-loop-interval": "registration/loops.md",
    "manual-handler-registration": "registration/enhanced-api.md",
    "rich-media-id-mismatch": "inline/inline-form.md",
}

_SUGGESTIONS = {
    "mcub-entrypoint": "Create a ModuleBase subclass or add main()/register() to the entrypoint.",
    "mcub-decorator": "Change the decorated handler from def to async def.",
    "mcub-handler-signature": "Use a class handler signature like async def handler(self, event): ...",
    "mcub-command": 'Use a literal name, for example @command("ping").',
    "mcub-command-conflict": "Rename the command or one of its aliases so every name is unique.",
    "locale-key": "Add the key to every locale file or change the string access to an existing key.",
    "locale-placeholder": "Make placeholder names match in every locale and pass missing keyword arguments.",
    "external-import": "Add the package to module.requires, [libs], runtime_modules, or an inline ignore when intentional.",
    "unused-require": "Remove the unused requirement or add the import that needs it.",
    "blocking-io": "Use an async API, asyncio.sleep(), or move synchronous work off the event loop.",
    "hook-placeholder": "Replace it with a documented hook placeholder such as {output} or {project_dir}.",
    "hook-path": "Fix the project-relative path or add the missing hook script.",
    "tool-missing": "Install the tool in the active environment or disable it under [lint.tools].",
    "tool-ruff": "Run cubkit lint --fix or resolve the Ruff diagnostics below.",
    "tool-black": "Run cubkit lint --fix to format the source with Black.",
    "tool-mypy": "Fix the type diagnostics or disable mypy for this profile.",
    "missing-await": "Add await, or explicitly schedule the coroutine with asyncio.create_task().",
    "missing-cleanup": "Store background task references and cancel them in async def on_unload().",
    "hardcoded-token": "Move the credential to protected configuration and rotate the exposed value.",
    "command-without-docs": "Add doc=... or doc_<language>=... to @command.",
    "locale-unused-key": "Remove the unused key or reference it with a static self.strings access.",
    "locale-dynamic-key": "Use a literal locale key or suppress this rule when dynamic lookup is intentional.",
    "asset-missing": "Add the file below [bundle].assets or correct the literal resource path.",
    "dependency-version-mismatch": "Install a compatible version or update the requirement constraint.",
    "deprecated-register-client": "Rename the argument to kernel; use client = kernel.client as a temporary alias.",
    "deprecated-config-file": "Read kernel.config.get(), assign kernel.config[key], and call kernel.save_config().",
    "redundant-validator-default": "Keep the default only as ConfigValue's second argument and remove validator default=.",
    "callbackquery-requires-bot-client": "Add bot_client=True to the callback query event decorator.",
    "invalid-event-type": "Replace the event name with one listed in the MCUB event registration table.",
    "conflicting-watcher-filters": "Remove one filter from each contradictory only_*/no_* pair.",
    "unfiltered-watcher": "Add watcher filters or move heavy I/O out of the every-message path.",
    "invalid-lifecycle-signature": "Use the documented async lifecycle signature for function-style or class-style modules.",
    "legacy-inline-form": "Replace kernel.inline_form(...) with kernel.inline.form(...).",
    "module-config-schema-missing": "Call kernel.store_module_config_schema(__name__, config) after creating function-style ModuleConfig.",
    "class-config-super-missing": "Add await super().on_load() to the class-style on_load override.",
    "config-default-invalid": "Choose a default accepted by the ConfigValue validator.",
    "duplicate-config-key": "Rename or remove the repeated ConfigValue key.",
    "missing-handler-types": "Annotate event parameters with Event from core.lib.types.",
    "inline-scope-missing": 'Set module.scope = "inline" in cubkit.toml.',
    "invalid-loop-interval": "Use a positive loop interval in seconds.",
    "manual-handler-registration": "Use kernel.register.event or watcher so MCUB cleans the handler on unload.",
    "rich-media-id-mismatch": "Add every tg://...id to rich_media or correct the HTML id.",
}


def documentation_for_rule(code: str) -> tuple[str, str | None]:
    """Return CubKit and optional MCUB documentation URLs for a lint code."""

    cubkit_url = f"{CUBKIT_RULES_URL}#{code}"
    mcub_path = _MCUB_RULES.get(code)
    mcub_url = f"{MCUB_DOCS_BASE}/{mcub_path}" if mcub_path else None
    return cubkit_url, mcub_url


def suggestion_for_rule(code: str) -> str:
    """Return a concise actionable remediation for a lint code."""

    return _SUGGESTIONS.get(
        code, "Read the linked rule documentation and correct the reported source."
    )
