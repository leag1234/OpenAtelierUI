# Compatibility-name decisions

Date: 2026-10-31

The review used only this repository and retained behavior/schema evidence. The reference inventory is Open WebUI 0.12.4, captured offline on 2026-10-25 in the parent evidence package `TASK-OPENATELIER-LATEST-001/readback-01/`. Its `intent.json` pins the version and image digest. Its `api-structure.json` records the paths, query fields and component references listed below. No later implementation source was consulted.

Compatibility takes precedence over cosmetic renaming. Names present in HTTP paths, parameters, JSON fields, environment configuration or OpenAPI component references remain stable because existing integrations and generated clients may consume them.

| Reviewed name | Decision | Evidence and compatibility reason |
| --- | --- | --- |
| Notes search parameters `query`, `view_option`, `permission`, `order_by`, `direction`, `page` | Keep | The 0.12.4 schema records these six query names on `/api/v1/notes/search`. Renaming them changes requests. |
| `NoteItemResponse`, `NoteUserResponse`, `NoteResponse`, `NoteListResponse` | Keep | The 0.12.4 schema exposes these exact component references for notes endpoints. Renaming them changes OpenAPI clients even if JSON stays equal. |
| Internal notes handler `search_notes` | Rename to `query_private_notes` | It is not imported outside tests. The route now pins the previous derived operation ID `search_notes_api_v1_notes_search_get`, so the HTTP/OpenAPI contract does not change. |
| `GroupMembersResponse` and `parent_group_id` | Keep | The 0.12.4 schema exposes the response component; the captured 0.12.4 database/schema and group readbacks expose `parent_group_id`. Clients and migration copies use the field. |
| `FileListResponse` | Keep | The 0.12.4 schema exposes this exact component for the file-list envelope. Existing compatibility tests also require `{items,total}`. |
| Model-history and model-access paths, parameters and operation IDs | Keep | Retained readbacks exercise `/api/v1/models/model/history`, `/api/v1/models/model/history/{version_id}` and `/api/v1/models/model/access/update`. Changing identifiers would churn generated clients. |
| Internal handlers `get_model_history`, `get_model_history_version`, `update_model_access_by_id` | Rename | They become `list_imported_model_versions`, `read_imported_model_version` and `apply_model_access_grants`. Each decorator pins the exact operation ID FastAPI derived before the rename. |
| `ChatsImportForm` | Keep | The 0.12.4 schema references this exact request component on chat import; the retained bulk-import black-box run validates the `{chats:[...]}` envelope. |
| `access_grants` | Keep | This is a request/response JSON field exercised by the retained 0.12.4 access readbacks and unchanged-plugin clients. Internal conversion helpers already use project-specific verbs. |
| `WEBUI_AUTH_TRUSTED_ROLE_HEADER` | Keep | It is an operator environment contract, default unset, covered by `ATLAS-IDENTITY-PROVENANCE.md` and `test_trusted_header_identity.py`. Renaming it would require deployment changes. |

The handler-only rename has no intended HTTP effect. A source-isolated test checks route paths, parameter order and pinned operation IDs. A full running-application OpenAPI comparison remains part of release qualification; this document does not claim that source inspection replaces it.
