# API-key configuration import provenance

This default-off importer slice implements only the three scalar settings in `TASK-OPENATELIER-KEY-CONFIGURATION-001`. It reads the measured source `config(key,value,updated_at)` rows without importing or executing the old configuration module. The selected source keys are `auth.enable_api_keys`, `auth.api_key.endpoint_restrictions`, and `auth.api_key.allowed_endpoints`. They map to `auth.api_key.enable`, `auth.api_key.endpoint_restrictions`, and `auth.api_key.allowed_endpoints` in one old-schema config record seeded from the pinned old literal defaults.

The importer accepts `enable_api_keys` only as a JSON boolean, so both true and false preserve the selected source effect. Endpoint restrictions must be JSON false, and the allowlist must be the JSON empty string. The three settings must each occur exactly once. Values are bounded and parsed with duplicate-JSON-key rejection. Parent, dotted, old-target, and environment-name aliases that could make the selected values ambiguous are refused with static diagnostics that do not echo config values. Nonempty restrictions, allowlists, invalid scalar types, malformed or duplicate JSON, missing/duplicate rows, unsupported table schema, and oversized values are refused before target creation.

The source does not change by default: without `--include-api-key-configuration`, all config rows retain the existing archive-only blocker behavior. With the option, exactly three selected rows are subtracted from the archive-only config count. When combined with `--include-named-tool-configuration`, its selected connection row and these three settings are merged into the same config record; unrelated rows remain archive-only. The importer emits a separate nonfatal prototype blocker and keeps `cutover_eligible` false. This does not import other config, change key credentials, activate keys, or establish complete key policy migration.

Tests use temporary synthetic SQLite databases and the actual CLI. They verify enablement true and false, exact nested mapping, one-record composition with a named tool connection, precise remaining config counts, default-off behavior, static non-value diagnostics, and refusal of missing, duplicate, aliased, malformed, oversized, wrongly typed, restricted, and unsupported-schema inputs before target creation.

Validation performed by this worker:

- `python3 validation/test_api_key_configuration_import.py` — 7 tests passed.
- `python3 validation/test_migrate_sqlite.py` — 51 tests passed.
- `python3 validation/test_imported_api_keys.py` — 11 tests passed.
- The root repository check, dashboard check, syntax compilation, and `git diff --check` are recorded with the final commit.
- No app or current-version runtime was exercised by this worker; primary owns behavior observation and independent application verification.

This is a source-only preparation prototype. Unselected configuration remains in the exact source archive, unsupported API-key policy shapes remain blockers, and no key or permission behavior is activated by this change.
