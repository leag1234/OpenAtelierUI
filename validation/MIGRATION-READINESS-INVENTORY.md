# Whole-copy migration readiness inventory

## Scope and evidence

This is a source-level classification of the 43 nonfatal blocker entries in the sanitized `whole-copy-05.json` summary. They contain 16 distinct codes; repeated per-row notices are grouped by code below. The same run records `cutover: false`, `converted_partial_copy_only`, exact source/template preservation, valid target SQLite integrity, and exact checks for selected chat fields, normalized message fields, tool specs/meta/global/valves, uploads, notes, and pins. The frozen `whole-copy-runner-05.py` explicitly selects `--include-history-sidecar`; the summary alone did not expose that option. A preserved archive is recoverable source material, not proof that the running target can use it.

Categories:

- **Omitted target data** — the candidate target lacks the stated source value or audit field; the exact source archive is retained.
- **Archive/API gap** — data is archived or present in target storage, but the old target's measured interface or behavior does not expose the equivalent operation.
- **Security reset** — the source value is deliberately not carried into the new authentication state.
- **Prototype qualification** — a bounded opt-in mapping/sidecar exists, but its complete target behavior or cutover is not established.

## Distinct blocker codes

| Code | Classification | Source-level finding and evidence boundary |
|---|---|---|
| `session_stamp_not_preserved` | Security reset | `migrate_sqlite.py:_inspect_source` reports a populated source stamp; `_copy_domains` maps only auth ID, email, password, and active state. Existing sessions are not imported. This is deliberate; fresh-login behavior is not established by the copy summary. |
| `user_profile_fields_prototype` | Prototype qualification | `migrate_sqlite.py:_inspect_source` and `_copy_domains` support only the bounded nullable bio/gender/date-of-birth opt-in and exact readback. The notice does not certify every profile field or the full profile API. |
| `function_history_prototype` | Prototype qualification | `_validate_function_history` validates a bounded function-history shape; `write_history_sidecar` copies it to an inert sidecar. Source-isolated route tests cover the gated read implementation (`validation/test_function_history.py`), but no current-consumer rendered revision journey, recording, restore, or cutover is qualified. |
| `api_key_record_metadata_not_preserved` | Omitted target data | `migrate_sqlite.py:_validate_api_key_import` copies the validated original key into the matching legacy user field; API-key record IDs and last-used/created/updated metadata stay in the archive. The opaque key value itself is not the omitted field. |
| `unsupported_nonempty_domain_config` | Omitted target data | `_inspect_source` leaves the remaining 369 config rows in the source archive; the target receives only defaults and explicitly selected mappings. Their target settings behavior is absent, not inferred from the row count. |
| `named_tool_configuration_prototype` | Prototype qualification | `migrate_sqlite.py:_parse_named_tool_connection` and `_copy_domains` map the selected no-auth named-tool connection; other config remains archived. The warning keeps cutover disabled and does not certify all connection or permission forms. |
| `api_key_configuration_prototype` | Prototype qualification | `migrate_sqlite.py:_parse_api_key_configuration` maps the three selected policy settings. Other settings and general policy behavior remain outside this slice. |
| `openai_configuration_prototype` | Prototype qualification | `migrate_sqlite.py:_parse_openai_configuration` maps the four selected endpoint settings. This is not complete provider/configuration migration. |
| `user_permissions_prototype` | Prototype qualification | `migrate_sqlite.py:_parse_user_permissions` copies the selected boolean map; only the bounded API-key feature check is behaviorally scoped. Other copied permission values are not qualified. |
| `group_membership_identity_and_time_not_preserved` | Omitted target data | `_validate_group_domains` maps direct member IDs into old group `user_ids`; `_copy_domains` does not map group-member row IDs or timestamps. Those audit fields remain in the archive. |
| `tool_metadata_fields_not_behaviorally_preserved` | Archive/API gap | `_validate_object_domains` identifies metadata keys outside `description`/`manifest`. `_copy_domains` copies the full tool `meta` JSON, while the old `ToolMeta` model exposes only those declared fields. Whole-copy05 confirms stored metadata equality, not API visibility or plugin behavior for extra keys. |
| `active_tool_history_not_preserved` | Prototype qualification | `_validate_object_domains` reports history outside the active target row. The whole-copy runner selects both `--include-function-history` and `--include-history-sidecar`. The latter's gated Tool/Model list/detail routes were compared over 852 calls per role-bound set, 139 inert revisions, and 3,415 exact assertions (`evidence/TASK-OPENATELIER-HISTORY-PROTOTYPE-001/comparison-02.json`). Recording new revisions, restoration, and cutover remain unsupported. |
| `active_tool_version_link_not_preserved` | Prototype qualification | `_copy_domains` does not map Tool `version_id` into the active row. The sidecar retains a verified pointer; the separately qualified direct Tool GET bridge exposes that pointer after freshness validation. This does not restore or activate a revision. |
| `active_model_history_not_preserved` | Prototype qualification | As with Tool history, `_validate_object_domains` reports history outside the active row. The whole-copy runner selects the Tool/Model sidecar, and read-only list/detail behavior is separately qualified by the same 852-call-per-set comparison. Recording, restoration, and cutover are not implemented. |
| `active_model_version_link_not_preserved` | Prototype qualification | `_copy_domains` omits Model `version_id`; the sidecar retains a verified pointer, and the separately qualified direct Model GET bridge exposes it after freshness validation. This is not a model-version restore mechanism. |
| `access_grant_identity_and_time_not_preserved` | Omitted target data | `_validate_object_domains` maps only the supported read ACL into `access_control`; `_copy_domains` does not preserve individual grant IDs or timestamps. The source archive retains them. This does not claim behavior parity outside the measured read profile. |

The 43-entry total is a blocker count, not 43 independent product gaps. It combines per-resource repeats with the 369 residual configuration rows represented by one blocker code. `whole-copy-05.json` demonstrates a partial copy and preservation checks, not that every nonfatal source value is active in the new application.

## Remaining migration qualification

Selected current-consumer chat, migrated identity, saved resources and restart journeys have since passed. The narrower revision-history gap remains **rendered revision use after whole-copy conversion**. Boot the converted copy with its selected history sidecar and gates, open a resource in the current consumer, expose the active version and one prior snapshot in an understandable UI, then restart and repeat the read before continuing ordinary work. Verify that the active resource remains unchanged and that another owner cannot see the private snapshot. If the consumer has no revision UI, record that concrete gap instead of building a restoration path. This tests the user journey that the historical API comparison did not cover.

The existing qualification remains narrowly read-only: exact list/detail behavior and current-pointer DTOs are measured, but revision recording after edits, restore/delete/diff/activation, all metadata shapes, rendered comprehension, and full cutover are not. Keep those notices; do not infer them from passing read calls or remove blockers based on this inventory alone.

## Limits

The blocker classifications above use own source, the sanitized whole-copy05 summary, the frozen runner selection, and the cited read-only history/current-pointer qualifications. Whole-copy source/target preservation is established for the selected values, but a current-consumer rendered revision journey, ongoing recording, restore behavior, extra Tool metadata access, session reset UX, and unsupported settings semantics remain unqualified. This inventory changes no importer behavior, defaults, or cutover status.
