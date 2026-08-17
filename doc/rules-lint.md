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
