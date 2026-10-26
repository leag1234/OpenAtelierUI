# Function history migration prototype

This is a default-off synthetic compatibility prototype for reading retained function revisions after a fresh SQLite copy. It does not restore, delete, diff, load, or execute historical source. The active function row remains the executable row.

## Measured input boundary

The permitted own-data observer against Open WebUI 0.12.4 used a frozen 21-call client. The selected fixture had 23 revisions and 22 parent links. The list endpoint returned a bare array of 20 rows on page one and three on page two, ordered by `created_at DESC, id DESC`; page zero and negative values matched page one, while a non-integer page returned 422. List rows carried revision/resource/parent/author/message/time fields and a live author-name projection, without snapshots. Detail returned the same fields plus the saved `{name, content, meta}` snapshot. A supplementary five-call own-data observation confirmed null commit messages, exact active `version_id`, and `meta.toggle: false` for both an inactive new function and an existing active synthetic function. Admin requests succeeded; member and anonymous history reads returned 401; missing resources/revisions and foreign-function revisions returned 404. These observations do not certify all roles, schemas, versions, or metadata.

The unchanged BSD candidate answered the 17 selected history requests with the SPA's 200 HTML fallback. Its active function content and activation flags remained intact, while its active detail omitted `version_id`. The source archive and existing function row remain the recovery path.

## Prototype behavior

`validation/migrate_sqlite.py --include-function-history` validates the measured history schema, revision identities, function and author references, same-function parent links and cycles, strict JSON-object snapshots, active version pointers, and existing byte/count bounds before target creation. It preserves snapshot text and stores the revisions in the existing opt-in `imported-history.sqlite` sidecar. Other history kinds are copied only when their separate `--include-history-sidecar` option is selected. The selected function-history blocker remains nonfatal and cutover remains disabled.

With `OPEN_WEBUI_IMPORTED_HISTORY_READS=1` set before application import, two admin-only GET routes expose list/detail data under `/api/v1/functions/id/{id}/history`. The list is capped at 20 rows and omits snapshots; detail includes only the inert snapshot. The existing admin function detail route adds the sidecar-verified active `version_id` and the measured boolean `meta.toggle`. Authorization and active-function lookup precede sidecar access. Missing, stale, corrupt, disabled, or unavailable sidecars do not become successful empty history. When the switch is off, the new routes are not registered and the existing function response model remains in place.

The sidecar manifest is bound to the copied active function owner and a fingerprint of the unchanged active row. Source history authors are validated as known users but never treated as authority. Snapshot bytes are retained verbatim and are never passed to plugin loaders.

## Limits and verification boundary

Only the selected own synthetic history shape is covered. Null commit-message behavior is now observed for the supplementary own fixture, but broad author edits, arbitrary metadata keys, empty-history semantics, unusual identifiers, larger histories, other database engines, and all frontend navigation states remain unqualified. There is no diff, restore, delete, activation, app-table migration, durable storage decision, or cutover certification. The primary agent owns installed-framework and pinned-image replay; local source tests do not substitute for those checks. No macOS or Windows user-facing revision workflow was exercised.

The local isolated checks passed: 8 function-history tests, 15 prior imported-history tests, and 51 importer regression tests. Python compilation for the changed modules and `git diff --check` also passed. The local test covers the actual FastAPI response serialization helper and extracted route authorization order; it does not run the complete application. A direct application-module import was unavailable in this checkout because the host lacks an existing `typer` dependency, and no package was installed. An initial bytecode compile attempted to use a cache location outside the writable workspace; rerunning with a temporary cache path passed. The earlier test-harness missing-`asyncio` error was corrected before this final run.

The active metadata validator currently requires a boolean `toggle` for each function that has retained history. The current response oracle directly measured `toggle: false` on two functions; broader per-function toggle coverage is pending primary review. If the selected source contains other active metadata shapes, the importer should refuse them rather than infer a value.
