# CubKit lint rules

Each CubKit lint diagnostic links to the matching section in this document and
prints a short `Fix:` suggestion in the terminal. MCUB-specific rules also link
to the relevant MCUB documentation chapter.

## mcub-entrypoint

The entrypoint must expose a class derived from `ModuleBase`/`Module`, or a
function-style `main()`/`register()` entrypoint.

## mcub-decorator

MCUB command, callback, watcher, and inline handlers must be asynchronous.

## mcub-handler-signature

Class-style MCUB handlers must accept `self` and the incoming event.

## mcub-command

Use a non-empty literal command name with `@command`.

## mcub-command-conflict

Command names and aliases must be unique within a module.

## locale-key

Every referenced string key must exist in all configured locale files.

## locale-placeholder

Locale placeholders must match across languages and be supplied to
`self.strings(...)`.

## external-import

External imports must be installed and declared in `module.requires` or `libs`.

## unused-require

Remove dependencies declared in `module.requires` that are not imported.

## blocking-io

Do not block the event loop with synchronous I/O inside `async def`.

## hook-placeholder

Use only documented CubKit hook placeholders.

## hook-path

Referenced hook scripts must exist within the project.

## tool-missing

Install an enabled external lint tool or disable it in `[lint.tools]`.

## tool-ruff

Resolve diagnostics reported by Ruff.

## tool-black

Format the project with Black or use `cubkit lint --fix`.

## tool-mypy

Resolve type diagnostics reported by mypy.

## missing-await

Await known async MCUB/Telethon calls and locally declared async functions, or
schedule them explicitly with `asyncio.create_task()`.

## missing-cleanup

Modules that create background tasks should retain their references and cancel
them from `on_unload()`.

## hardcoded-token

Credentials and private keys must not be stored in Python string literals.

## command-without-docs

Document commands with `doc=` or localized `doc_<language>=` arguments, for
example `doc_ru="Описание"`.

## locale-unused-key

Remove locale keys that are not referenced by a static string lookup.

## locale-dynamic-key

Dynamic string keys cannot be validated statically. Prefer literal keys or use
an inline ignore for intentional dynamic lookup.

## asset-missing

Literal paths passed to `resource()`, asset helpers, or `assets.read_*()` must
exist below the configured assets directory.

## dependency-version-mismatch

The installed distribution version must satisfy its `module.requires`
specifier.

## deprecated-register-client

MCUB function-style modules now receive `kernel`. Use `def register(kernel)` and
temporarily assign `client = kernel.client` when migrating old code.

## deprecated-config-file

Replace direct reads and writes of `kernel.CONFIG_FILE` with `kernel.config`,
`kernel.config.get()`, and `kernel.save_config()`.

## redundant-validator-default

Keep the default as the second `ConfigValue` argument. Do not repeat it in
validators such as `Boolean(default=False)`.

## callbackquery-requires-bot-client

MCUB callback-query events are routed through the bot client and therefore
require `bot_client=True`.

## invalid-event-type

The literal passed to `@register.event(...)` must be one of MCUB's documented
event names or aliases.

## conflicting-watcher-filters

Do not combine contradictory watcher filters such as `only_pm=True` and
`no_pm=True`.

## unfiltered-watcher

An unfiltered watcher executes for every message. Add filters before performing
network, subprocess, or file I/O.

## invalid-lifecycle-signature

Function-style lifecycle callbacks are async and receive `kernel`; class-style
lifecycle methods are async and receive `self` plus their documented arguments.

## legacy-inline-form

Use `kernel.inline.form(...)`; `kernel.inline_form(...)` is the old spelling.

## module-config-schema-missing

Function-style `ModuleConfig` must be exposed with
`kernel.store_module_config_schema(__name__, config)` for the config UI.

## class-config-super-missing

Class-style modules get config setup from `ModuleBase.on_load`. An override must
call `await super().on_load()`. A class without an override inherits it and is
valid without a manual schema registration call.

## config-default-invalid

A `ConfigValue` default must satisfy its Boolean, Integer, or Choice validator.

## duplicate-config-key

Every key inside one `ModuleConfig` must be unique.

## missing-handler-types

Annotate MCUB event parameters with `Event` from `core.lib.types` for editor and
type-checker support.

## inline-scope-missing

Modules using inline, callback, temporary inline, or rich-form APIs should set
`scope = "inline"` in `[module]`.

## invalid-loop-interval

Literal loop intervals must be greater than zero seconds.

## manual-handler-registration

Prefer MCUB `register.event`/`watcher` over direct `client.add_event_handler` so
handlers are tracked and removed automatically during unload.

## rich-media-id-mismatch

Every `tg://media`, `tg://photo`, `tg://video`, `tg://audio`, or
`tg://document` id in literal rich HTML must exist in the `rich_media` mapping.
