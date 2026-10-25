# Imported Tool and Model history read prototype

Recorded 2026-10-25 UTC. This is an opt-in, candidate-only prototype against the old BSD runtime. It does not qualify a migration, history-write behavior, cutover, durable storage architecture, or user interface.

## Inputs and measured storage choice

The source data was the permitted synthetic history projection, `history-only-fixture-01.json`, 126,793 bytes with SHA-256 `95f8e97c18270b2096f86c02f66fcf08092be1256f6d6b56f0728ffbf24e7264`. It contains three Tool and three Model resources, 69 Tool-history and 70 Model-history rows (139 total), and 65,412 bytes of original snapshot JSON text. No current database, archive, newer implementation, credentials, or private data was used.

Two disposable SQLite projections were measured before implementation: putting the history tables into a copy of the old application database, and placing those same history tables in a separate sidecar. Each read opened a fresh connection, checked the active row and its fingerprint, ran one list or detail query, then closed the connection. The loop was 250 reads for each of four operations. No concurrent writer was running.

| Measurement | History tables in app DB | Separate sidecar |
| --- | ---: | ---: |
| Base app DB | 28,672 bytes | 28,672 bytes |
| Resulting app DB | 184,320 bytes | 28,672 bytes |
| History sidecar | — | 159,744 bytes |
| Total resulting DB bytes | 184,320 bytes | 188,416 bytes |
| Added bytes over base | 155,648 bytes | 159,744 bytes |
| Combined generation time for both alternatives | 0.020812 s | included in same interval |
| Tool list p50 / p95 | 0.2677 / 1.3132 ms (2 rows) | 0.4093 / 2.3236 ms (2 rows) |
| Tool detail p50 / p95 | 0.2531 / 0.8863 ms | 0.4135 / 1.0114 ms |
| Model list p50 / p95 | 0.3221 / 4.1795 ms (3 rows) | 0.4105 / 1.3257 ms (3 rows) |
| Model detail p50 / p95 | 0.2971 / 3.2037 ms | 0.4167 / 2.4475 ms |

Environment: macOS 26.5, arm64, Python 3.9.6, SQLite page size 4,096 bytes. Process peak RSS was 16,056,320 bytes as reported by macOS `getrusage`; this is total process peak memory, including Python, JSON, temporary files, and both database alternatives. Added idle memory was not separately measured. These single-process synthetic timings are not a production or energy claim.

The throwaway alternatives used this history schema:

```sql
CREATE TABLE history_manifest (
  kind TEXT NOT NULL, resource_id TEXT NOT NULL, owner_id TEXT NOT NULL,
  current_version_id TEXT NOT NULL, fingerprint TEXT NOT NULL,
  row_count INTEGER NOT NULL, PRIMARY KEY(kind, resource_id)
);
CREATE TABLE tool_history (
  id TEXT PRIMARY KEY, tool_id TEXT NOT NULL, parent_id TEXT,
  snapshot TEXT NOT NULL, user_id TEXT NOT NULL, commit_message TEXT,
  created_at INTEGER NOT NULL
);
CREATE INDEX tool_history_page ON tool_history(tool_id, created_at DESC, id DESC);
CREATE TABLE model_history (
  id TEXT PRIMARY KEY, model_id TEXT NOT NULL, parent_id TEXT,
  snapshot TEXT NOT NULL, user_id TEXT NOT NULL, commit_message TEXT,
  created_at INTEGER NOT NULL
);
CREATE INDEX model_history_page ON model_history(model_id, created_at DESC, id DESC);
```

The exact base projection DDL was:

```sql
CREATE TABLE live_user(id TEXT PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE live_tool(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT,
  created_at INTEGER, updated_at INTEGER, version_id TEXT,
  persisted_json TEXT NOT NULL
);
CREATE TABLE live_model(
  id TEXT PRIMARY KEY, user_id TEXT NOT NULL, name TEXT,
  created_at INTEGER, updated_at INTEGER, version_id TEXT,
  persisted_json TEXT NOT NULL
);
```

Both alternatives used SQLite `busy_timeout=1000`. The detached sidecar is a provisional lab choice: it avoids adding tables to the old app DB and can be left disabled or removed with the candidate copy. It costs 4,096 additional bytes in this fixture and adds a second database open. This does not select a durable architecture.

## Implementation boundary

`validation/migrate_sqlite.py convert --include-history-sidecar` writes `imported-history.sqlite` inside the fresh target data directory; without the flag, conversion behavior is unchanged. The CLI writes only copied, previously validated history rows and a current-row fingerprint manifest. It never executes history content. The new runtime routes use the unchanged Tool/Model history URLs and are off unless `OPEN_WEBUI_IMPORTED_HISTORY_READS=1` is set. They require the app database to be the SQLite `DATA_DIR/webui.db` used by the importer.

Each route fetches the live resource and checks active owner/admin status before calling the sidecar reader. Tool nonowners retain 401 and Model nonowners retain 403, regardless of grants or authorship. Lists omit snapshots, use 20-row pages ordered by `created_at DESC,id DESC`, normalize nonpositive pages to page 1, and expose only the historical author name. Detail returns the saved JSON snapshot as inert data. Unknown resources/versions use the measured 404 shape. Missing, disabled, corrupt, stale, oversized, or unsupported database state returns 503 to an authorized caller; there is no archive fallback or empty-success fallback.

The detached, closed history sidecar is read with SQLite immutable mode. The live `webui.db` is opened read-only with ordinary SQLite locking and a one-second busy timeout, so a held write lock causes a clear 503 instead of bypassing the lock or serving stale bytes. A SQLite WAL is read only if the runtime SQLite build can safely open it in normal read-only mode; unsupported WAL state fails closed. Source/template readers use `Path.as_uri()` to encode paths safely.

The bounded reader refuses a history dataset above 8 MiB, a snapshot above 256 KiB, more than 10,000 revisions, nonregular/sidecar-ambiguous SQLite files, a custom DB path, a changed active persisted row, or an owner mismatch. SQL waits are capped at one second. The fingerprint covers the persisted active Tool/Model fields available in the old schema, not history-table writes. This is a snapshot import only: it does not record subsequent versions, restore/diff/delete versions, make historical code executable, expose full recovery archives, or qualify full cutover. Existing function-history, config, security/session, and other importer blockers remain blockers.

## Checks and limits

The isolated named tests exercise conversion with and without the opt-in sidecar, tied timestamp ordering and pagination including huge page numbers, inert detail snapshots, owner/authorship separation, cross-resource IDs, disabled/missing/corrupt/stale/oversized conditions, live database lock timeout, custom DB refusal, and extracted route execution proving owner/admin access, denial before reader invocation, missing-resource error bodies, and 503 translation. The full command `PYTHONPYCACHEPREFIX=/tmp/mdk-pycache python3 -m unittest -v test_migrate_sqlite test_imported_history` passes all 30 tests in 3.178 seconds. They do not replace primary-owned old-runtime HTTP replay, cancellation/SIGKILL testing, browser/visual review, or human acceptance. The first implementation commit was rejected before runtime staging for use of immutable mode on the active DB, wrong unknown-resource statuses, incomplete exception wrapping, and source-order-only route tests. This follow-up changes those areas while preserving the first commit. A failed initial host test import exposed that importing `open_webui` runs its package initializer and requires unavailable `typer`; the importer was corrected to load only the stdlib helper module directly. No dependency was installed. A first bytecode compile tried Xcode's protected default cache; checks use `/tmp` bytecode cache instead.
