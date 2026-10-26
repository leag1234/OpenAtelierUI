# Filter direct user lookup compatibility spike

This bounded source51 continuation implements the measured gap in the
sanitized `TASK-OPENATELIER-FILTER-SDK-SPIKE-001` contract. Its own black-box
fixture used a module-level `Users` import from an async `Filter.inlet`,
awaited the existing ID and email accessors, and then continued plain and SSE
answers. The current reference succeeded. The source51 candidate retained the
selected filter code, model and profile but returned400 for the three
authenticated completions; its anonymous401 remained. No raw error bodies,
profile data, or current implementation were inputs here.

The existing Pipe bridge remains separate and unchanged. A new
`adapt_filter_module` replaces only exact direct `Users` bindings in a loaded
Filter module, including aliases by object identity. Its separate lookup proxy
uses a task-local `ContextVar` set only around an adapted Filter handler. Sync
constructors and sync handlers see the exact native return value, `None`, and
exception immediately, including when invoked inside an event loop. For an
async handler, awaited ID/email lookups run through `asyncio.to_thread`, and
return the underlying `UserModel`, `None`, or exception. Other Users methods
delegate to the native singleton. The native Users object and application
binding are not modified.

The existing loader's Filter branch adapts the direct module binding before
constructing `Filter()`. The private compatibility marker is attached to that
loaded plugin's Filter class so it is visible on the instance returned by the
existing loader; no application-wide model or class is changed. Existing
filter dispatch sets sync/async lookup mode for the duration of each handler
call and resets it in `finally`, including exceptions and cancellation. The
two bounded ID/email accessors and the existing dispatch path are the only
affected behavior. The mode is task-local, so concurrent async and sync
handler calls do not share it.

The named source-bound command `python3 validation/test_filter_sdk_compat.py`
passes all8 tests. It executes the permitted synthetic fixture through the
extracted dispatcher, exercises direct imports and aliases, off-loop ID/email
lookups with a running heartbeat, native sync construction and dispatch inside
an event loop, mixed async inlet/sync outlet, concurrent task modes,
`None`/exception propagation, error/cancellation cleanup, unchanged Pipe
semantics, and the actual loader branch ordering. The existing Pipe suite
(`python3 backend/open_webui/test/test_plugin_user_compat.py`) passes8 tests;
priority tests pass7, and the file API suite passes8. `git diff --check` and
isolated syntax checks pass. Initial local harness attempts exposed a missing
synthetic Filter class, incorrect unpacking of the dispatcher result, and a
fixture email mismatch; those test setup issues were corrected, not hidden.

This remains a prototype pending primary-owned actual loader/cache/framework
controls and the unchanged current-client replay. It does not qualify dynamic
imports, re-exports, copied Users methods, Actions, Tools, internal imports,
arbitrary synchronous database lookups from inside async handlers, all plugin
SDK methods, loader side effects, full migration, or durable schema/default
adoption. The existing loader may deactivate a selected plugin when its
load/constructor fails; this patch preserves that behavior. Source51's failed
journey remains failed evidence until the independent corrected replay passes.
