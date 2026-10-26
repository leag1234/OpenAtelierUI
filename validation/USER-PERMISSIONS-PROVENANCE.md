# Imported user-permission compatibility prototype

This bounded BSD-3-Clause slice adds an opt-in path for preserving the selected flat `config` row `user.permissions` and enforcing its `features.api_keys` value during ordinary-member API-key creation.

The authorized synthetic policy observation showed two nested boolean maps with `features.api_keys` set to false and true respectively. It also recorded one existing group per account. The selected policy is a flat configuration row; the pre-existing group importer still requires empty group permissions and was left unchanged. The full permission map is copied exactly into the old target's existing `user.permissions` configuration object. Only the API-key creation check is in scope; copied workspace, chat, sharing, and other feature values are not certified as behaviorally migrated. The target remains a blocked partial candidate with cutover disabled.

`--include-user-permissions` is off by default. Without it, the existing importer continues to archive the nonempty configuration domain without mapping this row. With it, the importer requires one exact row, bounded JSON text, unique JSON keys, two-level boolean leaves, and a boolean `features.api_keys` value. It refuses missing or duplicate selected rows, parent/alias/dotted overrides, malformed or oversized JSON, and nonboolean values before creating a target. It composes with the existing named-tool and API-key configuration options without changing the pinned old defaults or `backend/open_webui/config.py`. Existing nonempty group permission overrides remain refused, including when this option is selected.

The existing `/api/v1/auths/api_key` route retains its global `ENABLE_API_KEY` check first. When `USER_PERMISSIONS.features.api_keys` is explicitly boolean, ordinary users are checked with the existing group-aware `has_permission` helper. Missing feature keys retain the old route path; an explicitly present nonboolean value is denied. The administrator path remains unchanged. A denial uses the existing API-key creation error. The same current synthetic eight-call observation showed member POST 403 when false and 200 when true; admin POST remained 200, anonymous POST 401, and password continuation remained 200. Candidate source baseline had returned 200 for the false member policy. Other roles and group-override cases remain unmeasured. The source tests do not qualify global configuration lifecycle or the full current HTTP response; the primary-owned frozen client and Hardy review are still required.

The user-visible effect is a direct denial when a migrated member lacks the selected feature permission, avoiding a later manual key-revocation repair. The flag is explicit and reversible by preparing a fresh candidate without it; it does not modify the original source. Tests use local synthetic SQLite fixtures and extracted old route code; they do not contact a service or handle real credentials.

Checks run by this helper:

- `python3 validation/test_user_permissions_import.py` — eight tests covering default-off behavior, exact copy, option composition, strict refusal before target creation, retained group refusal, and CLI reporting.
- `python3 validation/test_api_key_user_permission.py` — three extracted-route tests covering explicit true/false, absent/malformed values, global gate order, and the unchanged administrator path.
- `PYTHONPYCACHEPREFIX=/tmp/openatelier-key-permission-pycache python3 -m py_compile ...` — passed for the edited Python files. The initial unprefixed invocation could not create Python's default cache outside the writable candidate; the command was rerun with an isolated temporary cache.
- `git diff --check` — passed. This candidate checkout does not contain `tools/check_repository.py`; the monorepo documentation/dashboard checks remain with the primary owner.

These tests do not establish full permission migration, group-override semantics, mounted application behavior, or product acceptance.
