# R5 shared-name review report

Status: reviewed and source-tested on 2026-10-31.

`NAME-DECISIONS.md` records every name listed by the supervisor. HTTP paths, query/JSON fields, response/request component names and the trusted-role environment variable remain unchanged because the retained 0.12.4 schema/readbacks expose them and compatibility is a primary requirement.

Four internal handler names were changed to project vocabulary:

- `search_notes` to `query_private_notes`
- `get_model_history` to `list_imported_model_versions`
- `get_model_history_version` to `read_imported_model_version`
- `update_model_access_by_id` to `apply_model_access_grants`

Each route explicitly pins the exact FastAPI operation ID derived before the rename. Paths, methods, parameters, response models and status behavior were not changed. The focused AST contract test checks those invariants. The 21-test combined remediation suite and the 15 imported-history tests passed.

A full running-application OpenAPI comparison is deferred to release qualification; source checks alone are not represented as runtime evidence.
