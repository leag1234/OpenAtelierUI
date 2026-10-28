# Whole-copy migration readiness inventory

## Scope and evidence

This is a source-level classification of the 43 nonfatal blocker entries in the sanitized `whole-copy-05.json` summary. They contain 16 distinct codes; repeated per-row notices are grouped by code below. The same run records `cutover: false`, `converted_partial_copy_only`, exact source/template preservation, valid target SQLite integrity, and exact checks for selected chat fields, normalized message fields, tool specs/meta/global/valves, uploads, notes, and pins. It does not report an application/API journey for each blocker. A preserved archive is recoverable source material, not proof that the running target can use it.

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
| `function_history_prototype` | Prototype qualification | `_validate_function_history` validates a bounded history shape; `write_history_sidecar` copies it to an inert sidecar. The report explicitly leaves restoration and cutover unsupported. `imported_history.py` has bounded read functions, but that is not proof of the full native revision journey. |
| `api_key_record_metadata_not_preserved` | Omitted target data | `migrate_sqlite.py:_validate_api_key_import` copies the validated original key into the matching legacy user field; API-key record IDs and last-used/created/updated metadata stay in the archive. The opaque key value itself is not the omitted field. |
| `unsupported_nonempty_domain_config` | Omitted target data | `_inspect_source` leaves the remaining 369 config rows in the source archive; the target receives only defaults and explicitly selected mappings. Their target settings behavior is absent, not inferred from the row count. |
| `named_tool_configuration_prototype` | Prototype qualification | `migrate_sqlite.py:_parse_named_tool_connection` and `_copy_domains` map the selected no-auth named-tool connection; other config remains archived. The warning keeps cutover disabled and does not certify all connection or permission forms. |
| `api_key_configuration_prototype` | Prototype qualification | `migrate_sqlite.py:_parse_api_key_configuration` maps the three selected policy settings. Other settings and general policy behavior remain outside this slice. |
| `openai_configuration_prototype` | Prototype qualification | `migrate_sqlite.py:_parse_openai_configuration` maps the four selected endpoint settings. This is not complete provider/configuration migration. |
| `user_permissions_prototype` | Prototype qualification | `migrate_sqlite.py:_parse_user_permissions` copies the selected boolean map; only the bounded API-key feature check is behaviorally scoped. Other copied permission values are not qualified. |
| `group_membership_identity_and_time_not_preserved` | Omitted target data | `_validate_group_domains` maps direct member IDs into old group `user_ids`; `_copy_domains` does not map group-member row IDs or timestamps. Those audit fields remain in the archive. |
| `tool_metadata_fields_not_behaviorally_preserved` | Archive/API gap | `_validate_object_domains` identifies metadata keys outside `description`/`manifest`. `_copy_domains` copies the full tool `meta` JSON, while the old `ToolMeta` model exposes only those declared fields. Whole-copy05 confirms stored metadata equality, not API visibility or plugin behavior for extra keys. |
| `active_tool_history_not_preserved` | Archive/API gap | `_validate_object_domains` reports history absent from the active target row. `include_history_sidecar` can copy Tool history separately, but whole-copy05's sanitized summary does not say whether that option was selected. History reads use the gated sidecar routes rather than rows in the old application's `tool_history` table. |
| `active_tool_version_link_not_preserved` | Prototype qualification | `_copy_domains` does not map Tool `version_id` into the active row. The optional history sidecar can retain a verified pointer; `get_current_version_id` is guarded and fingerprint-bound. Restore/activation is not implemented by this path. |
| `active_model_history_not_preserved` | Archive/API gap | As with Tool history, `_validate_object_domains` reports history outside the active target row. The optional Tool/Model sidecar is separately selected; whole-copy05 does not expose its selection or a target API interaction. |
| `active_model_version_link_not_preserved` | Prototype qualification | `_copy_domains` omits Model `version_id`; an optional verified sidecar pointer/read path is not an active model-version restore mechanism. |
| `access_grant_identity_and_time_not_preserved` | Omitted target data | `_validate_object_domains` maps only the supported read ACL into `access_control`; `_copy_domains` does not preserve individual grant IDs or timestamps. The source archive retains them. This does not claim behavior parity outside the measured read profile. |

The 43-entry total is a blocker count, not 43 independent product gaps. It combines per-resource repeats with the 369 residual configuration rows represented by one blocker code. `whole-copy-05.json` demonstrates a partial copy and preservation checks, not that every nonfatal source value is active in the new application.

## Smallest next user-facing slice

First qualify one **read-only prior Tool/Model revision journey** from the copied target, because the current source already contains a bounded sidecar writer, stale-target fingerprint checks, pagination, and owner/admin GET routes. Do not add restoration or execution. On one known owned resource, a user should open the active resource, list its prior revisions, open one selected snapshot, then return to the still-active resource and continue ordinary use. After a process restart, the same list/detail response should remain available. A different owner must be denied before snapshot disclosure. Verify exact selected revision ID, parent, nullable commit message, timestamp, inert snapshot, active row before/after, and unchanged source archive. Also report whether the specific whole-copy option created the sidecar; the sanitized summary leaves that unknown. This single journey will distinguish an actually usable history read from an archive-only record without claiming restore semantics or full-history parity.

If that read journey fails, the next code slice should repair only the first demonstrated mismatch. If it passes, keep the history notice as a restoration/cutover limitation and choose the next implementation from a specific user-visible remaining setting, rather than mapping the 369 config rows generically. No notice should be removed based on this inventory alone.

## Limits

The blocker classifications above are based on own source code plus the sanitized whole-copy05 metadata summary. The summary's native evidence covers source/target preservation and selected exact values; it does not establish all current-versus-candidate HTTP behavior, session reset UX, Tool/Model revision APIs, extra Tool metadata access, or unsupported settings semantics. This inventory changes no importer behavior, defaults, or cutover status.
