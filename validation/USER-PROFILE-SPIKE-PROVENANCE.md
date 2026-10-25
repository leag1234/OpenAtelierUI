# User profile fields spike provenance

SPDX-License-Identifier: BSD-3-Clause

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
