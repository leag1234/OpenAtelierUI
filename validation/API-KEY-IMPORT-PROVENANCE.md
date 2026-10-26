# API key import spike provenance

This opt-in importer prototype is based on the bounded synthetic observations recorded for `TASK-OPENATELIER-API-KEY-IMPORT-SPIKE-001`. It does not consult or reproduce a newer implementation. The accepted source shape is one unexpired opaque API key per existing active `user` or `admin`, with the measured eight-column `api_key` table and the measured configuration policy. The nested `user.permissions` JSON value must contain `features.api_keys: true`; the flat `auth.enable_api_keys`, `auth.api_key.endpoint_restrictions`, and `auth.api_key.allowed_endpoints` rows must decode to the exact supported values. Conflicting dotted permission rows and duplicate policy keys are refused.

With `--include-api-keys`, preview and conversion validate the complete domain before any target is created. Conversion copies the original key bytes only into the same user's existing legacy `user.api_key` field. The default path remains unchanged and refuses a nonempty key table. Reports contain counts and blocker codes, never key bytes. An omitted key table or an empty one requires no key-policy rows.

The importer retains an exact source archive, but does not preserve API-key record IDs or usage/timestamp metadata in the target. Group permission overrides, key data outside null/empty text/JSON null/empty object, expiration, credentials represented in unsupported forms, duplicate keys or owners, inactive/pending/missing owners, unsupported schemas, and unrecognized policy shapes are refused before target creation. Even an empty JSON array is outside the measured data shape. The remaining unsupported configuration and metadata blockers keep `cutover_eligible` false; this is a narrow preparation experiment, not migration or authentication certification.

The focused tests use only synthetic key markers and temporary SQLite fixtures. The first run exposed test-fixture issues: one attempted to insert an unmeasured `api_key` source-user column, and another used the wrong `auth` fixture columns. The fixture was corrected to keep the measured source schema and exercise the import into the legacy target field. A second test initially used the old flat `user.permissions.features.api_keys` assumption; it was updated to test the measured nested config row and an explicit ambiguous dotted override. These were test/setup corrections; no source data or target from those runs was retained.

The primary's independent CLI oracle then found that the first implementation accepted `[]` as empty key data. The validator now rejects arrays, and a named empty-array refusal case confirms that no target is created. This was a bounded overacceptance defect in the opt-in importer, not evidence about an actual user key.

Validation performed by this worker:

- `python3 validation/test_imported_api_keys.py` — 11 named tests passed before the empty-array correction; the corrective run is recorded with the follow-up commit.
- `python3 validation/test_migrate_sqlite.py` — 51 tests passed, including the existing group, history, named-connection, and profile importer controls.
- No app was started and no live authentication request was made by this worker. The independent key-authentication observer and runtime integration remain primary-owned.

The measured current observer, as summarized by the primary, passed 11 controls across its 14-call run, including a seven-node graph and no key recreation. This is external evidence for context, not a test run by this worker. It does not establish group/API-key permission equivalence, metadata preservation, production safety, or cutover readiness.
