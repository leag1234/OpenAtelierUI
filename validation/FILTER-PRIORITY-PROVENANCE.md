# Filter priority compatibility slice

This bounded source50 continuation follows the sanitized own-fixture
qualification `evidence/TASK-OPENATELIER-FILTER-HOOKS-001/default-priority-qualification-01.json`.
The measured current profile had active synchronous and asynchronous filters
with declared defaults0 and10, no stored `priority` valves, and a controlled
reversed model `filterIds` order. The current reference continued to execute
the synchronous transform before the asynchronous transform after startup.
Source50 preserved the selected filter code, activation and model metadata but
produced three wrong-order plain/SSE completions. This is a bounded measured
compatibility gap; it does not establish a general ordering policy.

`get_sorted_filter_ids(model, request=None)` now retains the old positional
call shape. The two middleware request paths pass their existing request so
the function can reuse `request.app.state.FUNCTIONS` and the existing
`load_function_module_by_id` loader. It resolves each selected active filter's
stored `priority` first, then the declared default on its `Valves` model, and
falls back to0 when no default exists. It does not read a possibly stale
instance `valves` value from the request cache. Module load errors propagate
through the existing loader path; that loader may deactivate a failing
function as its existing behavior. Inactive and unselected IDs are filtered
before module loading. No middleware dispatch, handler, authentication, model
access, persistence format, schema, or dependency was changed.

The named tests in `validation/test_filter_priority.py` pass with
`python3 validation/test_filter_priority.py`. They cover reversed declaration
order, active filtering, warm cache reuse, stored override precedence and
removal/partial valves falling back to the declared default, defaultless
fallback0, positional helper use, plain and Pydantic-style valve defaults,
loader error propagation, and the two middleware request call sites. The
earlier source50 file tests also pass with
`python3 backend/open_webui/test/test_file_api_compat.py`. A first test run
incorrectly asserted loader call order even though the existing set-based
deduplication leaves equal-order discovery unspecified; the test now asserts
the selected set and zero warm loads. This does not change or qualify
equal-priority ordering.

Primary-owned actual loader/cache and mounted-app checks remain pending. These
source tests do not demonstrate the canonical filter corpus, cold/warm loader
counts under the installed application, sync/async HTTP response parity,
equal-priority ordering, global-versus-model precedence, valves beyond the
measured priority value, other filter hooks, full migration, or durable
adoption. Preserve source50's failed canonical behavior and the primary's
fresh source51 replay as separate evidence; passing local tests is not the
current-client acceptance result.

## Review correction

Primary review rejected the initial priority commit `26ecb888` before staging.
The first implementation loaded a selected filter module before checking for
an explicit saved priority. The correction now returns the saved value first;
context-free positional callers whose selected filters all have saved values
do not construct modules. A new focused test verifies that path records zero
loader calls. When any selected priority is absent, the request-aware paths
still obtain the declared default through the existing per-request cache and
propagate loader errors. The initial patch was not staged or run in the
mounted application; its review receipt remains in the parent evidence
directory. After correction, `python3 validation/test_filter_priority.py`
passes all7 named tests, and `git diff --check` plus isolated syntax compilation
pass. The corrected source still awaits primary's independent loader, cache,
and canonical replay checks.
