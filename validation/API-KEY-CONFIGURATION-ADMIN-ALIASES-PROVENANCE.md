<!-- SPDX-License-Identifier: BSD-3-Clause -->
# API-key admin configuration aliases

This narrow compatibility change adds the three observed current admin-config field names to the existing `/admin/config` GET and POST handlers:

- `ENABLE_API_KEYS` aliases `ENABLE_API_KEY`.
- `ENABLE_API_KEYS_ENDPOINT_RESTRICTIONS` aliases `ENABLE_API_KEY_ENDPOINT_RESTRICTIONS`.
- `API_KEYS_ALLOWED_ENDPOINTS` aliases `API_KEY_ALLOWED_ENDPOINTS`.

GET and successful POST responses retain the original singular keys and expose plural keys with the same values. Existing POST assignments and the `get_admin_user` dependencies are unchanged. The response mapping reuses its already-read singular values for the additional keys, avoiding additional lookups of potentially persistent settings. Pydantic accepts either spelling, including a payload containing both spellings when their values agree. A pre-validation check rejects conflicting values with HTTP 422 before the endpoint can write any settings.

The correction addresses a measured current-client payload mismatch for only these three settings. It does not implement the broader modern administrator configuration form, add other fields, change authentication or persistence, or claim full configuration compatibility.

Validation uses the installed FastAPI 0.115.6 and Pydantic 2.10.5. The source-isolated test extracts the actual `AdminConfig` model and GET/POST route definitions from `auths.py`, registers those definitions in FastAPI, and submits HTTP requests through `TestClient`. It checks both input spellings, false values, matching and conflicting dual spellings, output keys/values, zero writes on validation conflict, and unchanged admin dependencies. This test does not exercise the full application, persistent config backend, or current-version runtime.

The existing `get_admin_user` dependency remains the only authorization dependency on both routes. No new permissions, configuration defaults, schema, or dependencies were introduced.

Validation performed by this worker:

- `python3 validation/test_api_key_configuration_admin_aliases.py` — 5 tests passed.
- `python3 validation/test_api_key_configuration_import.py` — 7 tests passed.
- `PYTHONPYCACHEPREFIX=/tmp/openatelier-admin-alias-pycache python3 -m py_compile backend/open_webui/routers/auths.py validation/test_api_key_configuration_admin_aliases.py` — passed.
- `git diff --check` — passed.
- `python3 backend/open_webui/test/test_session_epoch_auth.py` — attempted, but its extracted `get_current_user` namespace omits `_VALIDATED_API_KEY_STATE_ATTRIBUTE`, producing 26 `NameError` errors across 23 tests. This unrelated harness file and its auth implementation were not modified by this slice.

The primary owns full application and current-version verification. No app runtime was exercised by this worker.
