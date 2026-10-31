# User profile fields spike provenance

SPDX-License-Identifier: BSD-3-Clause
Copyright (c) 2026 Gaël Duval
SPDX-FileCopyrightText: 2026 Ga�l Duval

This default-off experiment carries the measured nullable `bio`, `gender`, and
`date_of_birth` values through a fresh SQLite target copy and exposes them on
the existing signed-in session/profile paths. It keeps useful profile details
available to the same synthetic user's established server-side pipe lookup,
while the profile form continues to update only the authenticated user's ID.
Omitted and explicit-null fields both clear the three values, matching the
bounded current API observation. No account, sign-in, or session policy is
changed.

The importer accepts the explicit `--include-user-profile-fields` flag only
when the source has nullable `bio TEXT`, `gender TEXT`, and `date_of_birth
DATE` columns with supported values. It adds those columns inside the
transaction that populates a copy of the empty old template. The source and
template stay byte-identical. The readback compares IDs and all three values.
Without the flag, the previous unsupported-field reporting and target schema
remain unchanged. Both preview and conversion keep cutover disabled through a
named prototype blocker, even when the three fields are prepared successfully.

At runtime, `OPEN_WEBUI_IMPORTED_USER_PROFILE_FIELDS=1` enables validation of
the already-prepared SQLite columns at startup. The helper performs no DDL or
repair. The lifespan check does not claim that unrelated old application
import/startup machinery performs no writes before it runs. It returns an
extended Pydantic user model only through the existing
lookup when enabled; the SQLAlchemy `User` table and ordinary `UserModel`
remain unchanged so legacy constructors and insertion paths retain their
shape. One additional profile SELECT occurs on enabled lookup. The profile
update is one parameterized UPDATE containing the signed-in user's ID, name,
image, and three profile values. Default-off lookup, response, and update
branches keep their old behavior.

## Checks

- `python3 validation/test_migrate_sqlite.py`: 51 tests passed. This includes
  fresh-copy and exact-readback cases, default-off preservation, five-value
  public inspector compatibility, source/template immutability, invalid
  values/types and pre-target refusal, the CLI flag, and a reserved-`info`
  collision case that preserves existing JSON while reading the dedicated
  columns.
- `python3 validation/test_imported_profile.py`: 11 tests passed. These execute
  the helper against temporary SQLite databases, exercise real extracted
  `UsersTable.get_user_by_id` and auth route bodies, check the actual optional
  Pydantic form/model fields, confirm default-off avoids a profile schema
  read, verify one profile SELECT and one atomic self-profile UPDATE, and
  exercise the existing synchronous/awaitable pipe bridge with typed values.
  They also verify profile-data failures propagate through the ordinary
  password and trusted-header authentication wrappers instead of becoming
  invalid-credential results.
- `python3 backend/open_webui/test/test_plugin_user_compat.py`: 6 existing
  compatibility tests passed.
- The source-bound SQLite tests use a tiny local adapter for
  `sqlalchemy.text`; they do not constitute installed SQLAlchemy or full-app
  runtime validation. No app build, server activation, provider call, or
  hardware test was performed by this worker.

During test authoring, the first insertion placed the new importer test class
inside the neighboring named-configuration test class. The boundary was
corrected before the final runs above; both suites then executed under their
intended class names. A host `py_compile` attempt initially tried to write its
cache under the user Library and was denied; rerunning with a temporary cache
directory succeeded. Neither event changed application behavior or source
inputs.

Primary review of the first source commit also identified two enabled-path
integration issues: broad password/trusted-header catches could translate a
profile storage failure into an invalid-credential result, and the existing
socket session dictionary uses plain `json.dumps`, which cannot encode a
Python `date`. The follow-up defines and propagates `ImportedProfileError` only
for the opt-in profile path and serializes the typed DOB attribute as ISO text
in `model_dump`; the attribute itself remains a `date`. Tests cover both
paths. No Redis server or complete application process was started.

## Limits and trust boundary

The current API probe covered one synthetic account and values within the
provisional 8,192-byte biography and 128-byte gender caps. Those caps are
prototype resource bounds, not a claim that all current API constraints match.
Date handling accepts an exact valid ISO calendar date. The spike does not
preserve richer profile metadata, external identity state, session stamps, or
other user columns; those remain governed by the importer’s existing blockers.

The old in-process `Users.get_user_by_id(id)` library method has no caller
identity parameter and retains its existing arbitrary-ID behavior. When the
flag is enabled, its typed result may include the profile fields for the
requested row, including for existing internal callers that serialize that
typed result. The DOB field serializer keeps that internal model JSON-safe for
the existing socket session pool, whose `RedisDict` uses `json.dumps`. This
experiment does not claim confidentiality against trusted server-side plugin
Python that already has access to the user library/database, nor universal
redaction across every existing in-process caller. Reviewed nested HTTP DTOs
such as the old user-name/user response models omit the added fields. The HTTP
profile response/update path remains bound to the authenticated session, and
the request form cannot select a target user ID. No new plugin execution is
authorized.

The earlier default-off conversion on a separate synthetic snapshot produced
a partial target. A later strict actual-app client run failed during first
sign-in on the expected NULL-to-valid session-epoch initialization mismatch;
that failed receipt is retained. This slice does not change auth/session
stamps or claim that issue is resolved. This is a component spike only, not
migration certification, full runtime qualification, or a durable
schema/default decision.

## Follow-up harness and refusal-output correction

The primary regression replay found that the older AST-extracted session-epoch
route test did not seed the new `PROFILE_FIELDS_ENABLED` module global. The
route therefore raised `NameError` in that test namespace. The harness now
explicitly supplies `False`, keeping this legacy suite on the default-off path;
no application fallback or auth behavior changed.

The same review found that newly bounded profile lookup/read/update wrappers
chained underlying database exceptions into rendered tracebacks. Those wrappers
now suppress displayed causes while retaining the generic `ImportedProfileError`
message and rollback behavior. A synthetic trigger failure test checks that a
marker in the database exception does not appear in formatted output. This is a
bounded refusal-output check, not a claim that all application logging or
exceptions are sanitized.

The first host run of the new traceback assertion used a newer one-argument
`traceback.format_exception` signature and failed before checking the refusal.
The test now uses the three-argument form available in the repository's Python
runtime; the focused profile suite passes with the marker-redaction assertion
executed.

## Bounded email accessor continuation

The measured installed-pipe case awaits `Users.get_user_by_id(caller_id)`, then
awaits `Users.get_user_by_email(first.email)` and reads the same account's
prepared profile. This continuation adds the existing opt-in profile projection
to `UsersTable.get_user_by_email(email)` and the same Pipe-only proxy bridge
already used for ID lookup. The email remains an exact query value; this change
does not normalize it or change arbitrary in-process library access semantics.
The native application `Users` singleton remains directly bound; only the
adapted Pipe module receives the proxy. With profile fields enabled, the email
method intentionally returns the already-established enriched typed shape.

With the flag unset, the native email method remains on its original ORM path
and performs no profile SELECT. With it enabled, the method does one ordinary
email lookup and, for an existing account only, one selected profile SELECT;
the returned value uses the existing typed profile model and JSON-safe date
serializer. The proxy preserves positional and keyword `email` calls, native
model/`None`/exception outcomes in synchronous pipes, and an awaitable
thread-offloaded lookup for async pipes. A synthetic heartbeat test checks that
an async pipe's blocking email accessor does not stall unrelated loop work.

The host source tests pass, but current-installed-pipe replay and actual
SQLAlchemy integration remain primary-owned and pending. The earlier
partial-migration replay remains a failed baseline: before this change its
selected installed pipe returned empty `200`/`DONE` results after three
non-awaitable `UserModel` errors. This patch addresses only the measured email
accessor gap; it does not claim the frozen end-to-end replay passes, broader
plugin-import compatibility, full migration/cutover, or a durable schema
decision. No routes, auth/session policy, importer, DDL, default, or dependency
changed.

The first compatibility-file run executed only its six pre-existing registered
tests because the file uses an explicit `TESTS` tuple. The two new email bridge
tests were then added to that registry and the final run executed all eight.

## Bounded file response continuation

The FILE-API source49 slice adds the exact opt-in flag
`OPEN_WEBUI_CURRENT_FILE_API=1` to only the no-page `GET /api/v1/files/` and
`GET /api/v1/files/{id}` response branches. With the flag unset, the old list
array and `FileModel` metadata serialization remain in place. With it enabled,
the list wraps the already-selected `FileModelResponse` items in an object with
`items` and integer `total`; it does not assert behavior for page parameters or
unmeasured admin-list scope. The detail response uses a new public DTO with the
measured metadata keys and omits `access_control` from that public projection.
The ORM model and stored ACL remain intact, and the old owner/admin/read-grant
checks and 404 behavior run before serialization.

The source-bound file route test covers the flag-off shapes, enabled list keys
and count, ACL-free public metadata with retained internal ACL, owner/admin and
read-granted success, denied and missing 404 behavior. Its first run asserted
that the owner-filtered legacy list was the exact store list object; the old
store method correctly returns a new list containing the same row objects. The
assertion now checks that observed legacy behavior. Source48’s measured
current-client replay remains the baseline: saved-file identity/bytes/access
and follow-up operations were preserved, while the top-level list envelope and
extra public metadata field differed. This slice does not claim the corrected
frozen replay, full pagination, complete admin listing, or full file API
compatibility until primary’s independent review and mounted replay finish.

The first `py_compile` invocation tried to create bytecode beneath a protected
user Library cache and failed with `PermissionError` before compiling either
file. No application files changed from that attempt. The syntax check was
rerun with `PYTHONPYCACHEPREFIX` pointed at a temporary cache directory.

## FILE-API review correction

Primary review rejected the initial FILE-API commit `5ae93dd` before staging.
Its enabled list used `extra="allow"` with the full internal file dump and
therefore exposed `path` and `access_control` in each item; the corresponding
test repeated that incorrect expectation. It also returned raw `JSONResponse`
objects, bypassing FastAPI response validation and leaving its generated schema
on the legacy shape.

The correction projects list items to only the fields declared by the existing
`FileModelResponse`, while leaving each stored `FileModel` and ACL untouched.
A new `FileListResponse` DTO provides the enabled `{items, total}` envelope.
Both GET route decorators select their response model once from
`OPEN_WEBUI_CURRENT_FILE_API` at module import, and the routes return ordinary
Pydantic DTOs. The detail DTO keeps the authorized `path`, omits only the
internal ACL field, and retains required-but-nullable timestamp fields like
`FileModel`. Source-bound checks now verify the enabled response-model
registrations, list exclusions/internal ACL preservation, public metadata
shape, and default-off route shapes. Primary’s installed FastAPI/Pydantic wire
and generated-schema replay remains pending; the initial commit’s failed
expectation and rejection are retained here.

## FILE-API bounded pagination continuation

Source50 adds opt-in pagination only to the current-profile `GET
/api/v1/files/` branch. Its native `page` parameter defaults to1 and has a
minimum of1; the legacy branch remains registered without a `page` parameter
or changed response model. The handler calls `FilesTable.get_files_page`,
which applies the existing owner filter (or no filter for admins), counts the
selected rows, orders by `updated_at DESC, id DESC`, and applies offset and a
fixed limit of50 before materializing models. The existing public list
projection remains limited to declared `FileModelResponse` fields. With
`content=false`, it copies each data dictionary before removing `content`,
leaving the stored model unchanged.

The source-bound suite passes all8 named tests via
`python3 backend/open_webui/test/test_file_api_compat.py`; `git diff --check`
also passes. It executes the actual conditional route-registration block and
the actual extracted query helper against controlled fakes, checking default
off route/schema shape, enabled response registration and page constraint,
member/admin filtering, order, count, bounded page sizes, and copy-only content
omission. Early focused runs exposed test-harness issues (a `Query` sentinel
used as a direct-call default, an expectation inconsistent with descending
order, and missing synthetic AST locations); these were repaired, and the
final named run executes all8 tests successfully.

The measured profile is page size50, page numbers beginning at1, and ordering
by `updated_at DESC, id DESC`. Current synthetic observations included member
pages of50/16/empty from66 files and admin pages of50/17 from67. The source
tests do not qualify actual SQL query count, generated OpenAPI, HTTP422 before
storage access, larger-data performance, concurrent updates during paging,
other database engines, or complete administrator-list compatibility. Those
remain primary-owned installed-library and mounted-runtime checks. Source49's
failed page behavior and earlier commits remain preserved; this bounded
continuation does not claim full file API or migration acceptance.
