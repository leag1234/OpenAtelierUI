# Active object import prototype provenance

This prototype extends the existing stdlib-only fresh-target importer to prepare active Tool and Model rows from a stopped SQLite snapshot. It does not execute object source or instantiate the application. The source snapshot remains byte-for-byte archived outside the target data root; output always requires a new directory and uses the trusted old-runtime template.

The converter preserves active IDs, owners, names, tool content/specs/metadata/valves, model base IDs/params/metadata/active flags, and timestamps where the old schema can represent them. Missing grants become private `access_control={}`. A single user/read grant maps to `read.user_ids`; a sole user `*`/read maps to the old runtime's public `NULL`. Group, write, duplicate, conflicting-public, dangling, malformed, and unknown grants are refused before a target is created. The converter also validates object owners, JSON shapes, history resource/parent/current-version links, and active-version uniqueness. A history author's user ID must be a nonempty known source user, but it may differ from the active resource owner: current API behavior records the editor/author, not necessarily the resource owner. Author IDs remain archived data and are never used as live read/write permission principals.

These mappings preserve the measured read-access meaning but do not preserve grant IDs or creation times. The old schema has no compatible history API or version-link representation. History/version rows and grants therefore produce explicit nonfatal cutover blockers even when a partial target can be prepared. Tool metadata keys ignored by the old response model and nonempty tool valves are also reported as blockers. A prepared target with blockers is for isolated inspection only and must not be normally started or cut over. The immutable source archive is the recovery record; there is no reverse importer for post-import writes.

The checked-in test fixture is synthetic. Its tool content contains a marker-writing function body, and the test verifies the marker file is absent after preview/conversion. That establishes non-execution for the exercised code path, not a general sandbox guarantee for another importer.

Run the named suite from `validation/` with:

```sh
python3 -m unittest -v test_migrate_sqlite.py
```

The tests exercise private, targeted-user-read, public-read, unsupported rights, dangling grants, missing versions, known non-owner history authors, unknown/malformed authors, valid-but-unpreserved history, original/archive hashes, fresh-target behavior, and existing importer safeguards. Passing these source-isolated tests does not establish HTTP authorization, useful model inference, all metadata/API behavior, crash/cancellation behavior, production migration safety, or cutover eligibility for the supplied rich snapshot. The independently captured rich snapshot includes group grants and resource history/version data and is expected to be refused or remain blocked; primary owns target SQL and runtime validation.

For users, the prototype's preview must clearly distinguish “active rows prepared” from “migration complete.” Conversion is reversible only by returning to the preserved original database before accepting new writes; after writes, they require a separately designed export/reconciliation path. No automatic activation occurs. This experiment does not choose a durable product default or authorize a real migration.
