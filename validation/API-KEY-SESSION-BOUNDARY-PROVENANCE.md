# API-key session boundary provenance

This narrow source change addresses the measured `GET /api/v1/auths/` boundary in `TASK-OPENATELIER-API-KEY-SESSION-BOUNDARY-001`. The primary's sanitized black-box result is that a valid API key receives `403` and no session JWT from the current reference, while the old candidate returned `500`. The independent key-import observer separately passed its selected key-authentication controls; that observer is not a test run by this worker.

After the existing `get_current_user` API-key branch completes its ordinary key lookup and activity update successfully, it stores an app-owned private marker on that request's state. The marker contains only the validated user ID and an in-process sentinel; it contains no key bytes or client-supplied selector. The dependency resets the marker at each entry, including when the copied session-epoch option is disabled. The session GET checks the marker against the resolved user's ID and returns `403` before token creation, cookie mutation, permission lookup, or profile projection. Other routes and API-key lookup behavior are left to their existing paths.

The named tests extract the old source functions and route body, run them with the installed FastAPI/Starlette request, response, credential, and HTTP exception types, and stub only the user store/config/token/policy collaborators. They verify the successful native lookup path, rejected/disabled/restricted keys, marker reset and identity checks, zero session side effects for a valid API key with profile/epoch options both on and off, and retention of a supplied validated JWT token and expiry. Route decorators and full app registration are not recreated; no full-app result is claimed.

Validation performed by this worker:

- `python3 validation/test_api_key_session_boundary.py` — 6 named extracted-source tests passed.
- `python3 tools/check_repository.py` — passed (33 required paths, 890 Markdown files).
- `python3 apps/dashboard/server.py --check` — passed (M0–M9 order, rubric, gate types, and evidence references).
- `PYTHONPYCACHEPREFIX=/tmp/openatelier-api-key-boundary-pycache python3 -m py_compile backend/open_webui/utils/auth.py backend/open_webui/routers/auths.py validation/test_api_key_session_boundary.py` and `git diff --check` — passed.
- No full app, live key, real user account, or modern implementation was used by this worker. Primary owns the unchanged full-app and imported-key regression.

The change establishes only this request-local session-promotion refusal. It does not qualify other key endpoints, the full authentication stack, concurrency across app middleware, or production key/session policy. The marker is process-local request state and is reset before each dependency evaluation; a caller cannot set it through URL, body, header parsing, or raw key copying.
