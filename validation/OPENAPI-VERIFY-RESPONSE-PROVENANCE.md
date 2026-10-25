# OpenAPI verify response correction provenance

This bounded BSD-3-Clause change addresses one observed verify-response shape difference from the approved OpenAPI fixture comparison. The unchanged old `get_tool_server_data` loader returns an internal envelope containing `openapi`, `info`, and converted `specs`. The successful admin verify route now returns the envelope's `openapi` document, matching the measured raw-document response.

The route keeps its admin dependency, URL concatenation, bearer/session token selection, loader call, exception handling, and configuration state unchanged. No generic loader caller, endpoint, catalog, registration, config value, ACL, or persistence behavior changed. The envelope remains the loader's internal return format for other old callers. Compatibility of any external client that relied on the old verify envelope is not qualified; the observed current API contract takes priority for this route, and a separate caller adapter may be needed if a real consumer is found.

## Verification

Run the source-extracted, dependency-free tests from the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 work/openatelier/candidate/validation/test_tool_server_verify_response.py
```

The five tests pass. They execute the actual route function body with injected loader/request/HTTP-exception stubs and cover exact raw OpenAPI document equality, no-auth behavior, bearer and session token selection, literal URL joining, and the existing loader-error-to-400 mapping. A source assertion confirms the admin dependency declaration remains. FastAPI routing, actual auth middleware, network fetch, configuration writes, and mounted-image behavior require the primary-owned replay; these tests do not claim those results.

The first run failed because the isolated AST extraction retained the FastAPI route decorator without defining a router. The test now removes only that decorator for direct body execution and separately checks the source dependency declaration. No application code change was needed for this harness correction.
