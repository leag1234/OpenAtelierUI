# Flat group membership and read-grant preparation

Recorded 2026-10-25 UTC. This is a bounded importer/read-ACL prototype for the measured flat default group profile. It is not a complete group API migration or cutover qualification.

## Behavior and source profile

The authorized 105-request 0.12.4 synthetic oracle observed one flat group, one direct member, and Tool/Model group-read grants. Adding the member changed direct resource reads from 401 to 200; removal changed them back to 401; re-adding restored 200 on the next request. Authorized member responses kept Tool content and Model parameters redacted. Group detail/member endpoints remained admin-only in this fixture. Group membership did not grant Tool or Model history access. These observations do not cover hierarchy, nonempty group permissions, write grants, wildcard principals, or general account data.

The source group profile is limited to `parent_group_id = null`, `permissions = {}`, `meta = null`, and `data = {"config":{"share":"members"}}`. Group IDs, known owners, names/descriptions, timestamps, and JSON data are copied to the old `group` row; direct `group_member` user IDs become the old embedded `user_ids` JSON list. Current membership row IDs and timestamps remain only in the byte-exact source archive and therefore block cutover. Group/read grants map to `access_control.read.group_ids` and may coexist with known targeted user/read grants. Grant IDs/timestamps remain archive-only and block cutover.

The old runtime searches embedded membership with `LIKE '%"<user id>"%'`. Before preparing a group, the importer requires every known account ID to be ASCII, free of LIKE wildcards and JSON-escaped delimiters/control characters, and unique under case folding. This makes the old quoted substring lookup exact for the validated account set. IDs containing `%`, `_`, quotes, backslashes, controls, non-ASCII characters, or case-fold collisions cause fatal refusal rather than a possible false grant or lost membership.

## Verification and limits

`validation/test_migrate_sqlite.py` checks a fresh old group table, exact group/member projection, mixed user+group read ACL, preserved source/template/archive hashes, and false-positive boundaries such as `u1` versus `u10`. Fatal no-target cases cover nondefault data, metadata or permissions, parent hierarchy, malformed schemas/timestamps, missing owners/users/groups, duplicate memberships, group wildcard/write grants, old target schema absence/mismatch, and ambiguous LIKE identities (case collision, wildcard, escaped quote).

The importer still blocks cutover because membership/grant row identity and timestamps are archived only, alongside the existing unrelated migration blockers. Current group/member REST operations are not bridged in this slice; current clients cannot use their add/remove endpoints against the old runtime without a separate API batch. Existing old admin group-update controls can edit embedded IDs, but that does not establish current-client compatibility. No list/catalog/UI behavior, group form, schema, history permission, or resource API changed. Primary owns the isolated CLI/SQL and direct-read role replay; these local tests are not runtime qualification.
