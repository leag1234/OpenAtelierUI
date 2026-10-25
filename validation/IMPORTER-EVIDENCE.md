# SQLite importer prototype evidence

This is a bounded synthetic-data preparation experiment, not a migration or cutover qualification. The importer uses Python's standard library only. It opens source and template databases read-only with SQLite immutable mode, refuses live/WAL-ambiguous inputs, never executes source DDL/triggers/plugin content, and creates only a new target directory plus a separate sibling source archive.

## Commands

Read-only preview:

```sh
python3 validation/migrate_sqlite.py preview \
  --source-db SOURCE.db \
  --uploads-root UPLOADS_DIR \
  --empty-old-target-template EMPTY_OLD_TEMPLATE.db
```

Fresh-target preparation:

```sh
python3 validation/migrate_sqlite.py convert \
  --source-db SOURCE.db \
  --uploads-root UPLOADS_DIR \
  --empty-old-target-template EMPTY_OLD_TEMPLATE.db \
  --new-target-dir NEW_TARGET_DIR \
  --runtime-data-root /app/backend/data
```

Exit 0 means preview found no known blockers or conversion produced a candidate eligible for independent review. Exit 3 is an explicit blocked/fatal result; a blocked partial candidate may have been prepared for isolated API testing, but it is not eligible for startup or cutover. Exit 2 reports an input/operational refusal. Existing targets are never overwritten.

The old application database filename is `webui.db`. The persisted upload path uses `RUNTIME_DATA_ROOT/uploads/<id>_<filename>`; the physical bytes are in `NEW_TARGET_DIR/uploads/`, ready to be mounted under the configured runtime data root. The source archive is published at the reported sibling path `TARGET_NAME.source-archive-<random>`, outside the app target directory. It contains an exact source SQLite copy and exact uploaded-file copies. The application target contains only `webui.db`, copied uploads and `report.json`. The JSON report records source/template/input/output hashes and sizes, blockers, readback row counts, elapsed time, process peak RSS, and whether provisional output/time/memory budgets were met.

## Observed synthetic snapshot

Input source snapshot SHA-256 `cb73b9acbf3dc4e9625afe9e6299d155b41322f05af14295270e4e39fccc8d3c` (778,240 bytes), original upload SHA-256 `6a2e70a29ce84bfde44dad1b084b79ec3f160f8f5f2ac7b57b17900f2f0cb10a` (105 bytes), and pristine old template SHA-256 `1080bfaf8a53c0c9baedd9bdd8fe6679e098444b892ecc071182bf2134eea76b` (208,896 bytes) were read from stopped synthetic snapshots. The preview returned exit 3, status `blocked_partial_candidate`, `cutover_eligible: false`, and blockers for 375 config rows, 4 function-history rows, nonempty auth session stamps, 4 function revision links, message fields absent from the old target representation, and one `last_read_at` value. The config/history/session-stamp domains are archived only and are not behaviorally migrated.

One local conversion produced a blocked partial target for the primary-owned isolated API-only lab exercise. It returned exit 3, imported counts auth 2/user 2/chat 1/file 1/function 4, retained the source archive outside the app target, and preserved the original source/template hashes. It is not eligible for normal startup, cutover or production use. Final host measurement: 0.040214 seconds elapsed, 20,922,368 bytes process peak RSS, and 997,437 bytes output including the report; provisional limits were 30 seconds, 256 MiB process peak RSS, and 4x input plus 16 MiB output. All were within limits. These are one-run host observations, not production performance guarantees.

## Tests and limits

Run the actual named suite with:

```sh
python3 validation/test_migrate_sqlite.py
```

Thirteen stdlib tests cover read-only preview/source invariance, unsupported-domain blockers, exact source/upload archive copies outside the app root, IDs/password bytes/branches/function source/old migration marker/file bytes, refusal of inconsistent chat representations, retained injected-interruption attempts with a fresh retry, no overwrite of an existing target, missing uploads, duplicate chat IDs, MFA/grant/group data, unknown schemas/template triggers/WAL sidecars, owner mismatches/orphan messages/cycles, email mismatches, traversal paths and a corrupt SQLite refusal. The primary owns integration on the pinned old BSD runtime and independently exercises account sign-in, branches, uploads, functions, continuation, and member authorization.

Unsupported or incomplete semantics include config behavior, function revision/history behavior, session-stamp invalidation, chat fields absent from old schema, MFA and security membership/grant data, unknown schema/domain fields, reverse export of post-import writes, and production recovery/cutover. Current nonempty unsupported data blocks eligibility even when the byte-exact source archive is complete. A target that fails fatal validation is not created; Python exceptions injected at defined failpoints retain an attempt with a failure receipt. This is not evidence for SIGKILL/power-loss recovery, paired archive/target atomicity, concurrent writers, source snapshot coordination, or cancellation behavior. Human review and the primary-owned API/security checks remain required.

## Opt-in named tool configuration preparation

The closed source42 preview03 projection contained378 configuration rows, including one measured no-auth named OpenAPI connection with an existing ordinary-user/read grant. The selected connection is now supported only behind `--include-named-tool-configuration` (or `include_named_tool_configuration=True`). The parser accepts exactly the measured local fixture shape, one selected key row and one existing role=`user` read principal. It rejects duplicate or oversized JSON, duplicate selected rows, unknown fields/schema, credentials, nonlocal URLs, multiple connections, public/group/write grants, and absent/admin principals before target creation. `headers` may be null or empty; `forward_cookies` must be false. Reports contain only opt-in status, counts and pinned source/default hashes, never connection values or principal IDs.

The opt-in reads the old `DEFAULT_CONFIG` assignment through `ast.literal_eval` after matching the pinned `config.py` source SHA-256 `e86c2da3487ddf9d9ee9fe5bafc81f5e96d8bc8a800a5632e3387bec6e57d694` and canonical defaults SHA-256 `7870936571d911d4d4580b924d7b187ac9b5d01121299a1947f5d1ccfc832dd8`. It does not import the runtime config module. On the target copy, it seeds one old-schema config row containing those defaults plus only `tool_server.connections`. The original template remains empty and unchanged. Other source configuration rows remain archive-only blockers; the opt-in adds an explicit prototype blocker, so the report remains `cutover_eligible: false` even if this is the sole configuration row.

The dependency-free importer suite now runs37 named tests with `python3 validation/test_migrate_sqlite.py`. Added coverage checks default-off non-materialization, actual CLI preview/convert with the opt-in, exact selected ID/read principal and default preservation, source archive equality, report privacy, pure no-import default loading, duplicate/oversized/malformed/missing selections, unsupported credential/URL/grant/principal cases, and source/template/default-hash refusals before target creation. An initial module-form test invocation failed test-module discovery; early source tests also caught treating the selected value as an object instead of the measured one-element list and an imprecise unsupported-grant refusal code. Those defects were corrected; the documented direct script command then ran the complete suite successfully. This evidence is source-bound SQLite testing only. The primary owns isolated runtime startup, role replay and unchanged plugin continuation; the prototype does not establish complete configuration migration, normal-start eligibility or cutover readiness.
