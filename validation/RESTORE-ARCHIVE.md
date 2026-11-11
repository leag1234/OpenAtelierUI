# Restore a v0.11.3 migration archive

`openatelierui-restore-v0113` reconstructs a fresh data directory from the sibling archive produced by `openatelierui-migrate-v0113`. It never activates the result, changes the archive or overwrites an existing path.

```sh
openatelierui-restore-v0113 \
  --archive-dir /work/copied-data.source-archive-IDENTIFIER \
  --new-target-dir /work/restored-v0113-data
```

Both paths must be absolute. The target and its ancestors must not overlap the archive, and the target must not already exist. Success creates:

- `webui.db`, byte-identical to the complete archived source database;
- `uploads/`, with every archived upload copied and hash-verified.

Directories are private mode `0700`; the database and uploads are mode `0600`. The command verifies SQLite integrity, input stability, file count, byte limits and the complete upload manifest before atomically publishing the new directory. Native no-replace publication is required and currently supported on Linux and macOS; other platforms fail closed.

The JSON success status is `restored_copy_only` with `cutover: false`. A refusal exits with status 2 and a stable `reason`; no destination is published. The command does not start OpenAtelierUI, change mounts, reverse writes made after migration or decide which data root an operator should activate.

Keep the original data volume until the restored copy has been independently reviewed. Restoration is a direct-control fallback, not an automatic cutover or production recovery claim.
