# Whole-copy migration coordinator prototype

This opt-in validation tool composes the existing full-profile SQLite importer with the private-notes converter on a new copy. It does not switch, activate, or overwrite an installation. Preview creates a mode-0700 inspection workspace and leaves the target path absent; conversion writes a new target directory with `cutover: false`.

The coordinator retains an exact full source database copy and upload copy. It validates note rows, same-owner pins, source schema, and the absence of unsupported note grants or external foreign-key references before making a second staging database. Only validated `note` and `pinned_note` rows are removed from that staging database, inside a transaction; no grants, source schema, or source rows are deleted. The unchanged core importer runs on that staged copy. The existing notes converter merges the validated notes into the new target, followed by exact row and pin readback checks. Inputs are hashed before and after processing; failed workspaces and intermediates are retained for diagnosis.

The local synthetic fixture exercises a branched source shape with two notes, raw JSON text and SQL NULL values, one same-owner pin, uploads, and an empty old template. It checks preview isolation, successful composition, exact note/pin values, zero-note handling, and refusal of note grants or a foreign pin before staging trim.

Checks run:

- `python3 -m unittest validation.test_migrate_atlas_copy -v` — 5 tests passed.
- `PYTHONPATH=..:. PYTHONPYCACHEPREFIX=/tmp/mdk-atlas-copy-pycache python3 -m unittest test_migrate_sqlite test_import_private_notes test_migrate_atlas_copy` from `validation/` — 71 tests passed.
- `PYTHONPYCACHEPREFIX=/tmp/mdk-atlas-copy-pycache python3 -m py_compile validation/migrate_atlas_copy.py` — passed.

One earlier `py_compile` attempt used the platform's restricted default bytecode-cache path and failed with `PermissionError`; rerunning with a temporary cache passed. During implementation, a refactor briefly omitted the `shutil` import and the focused suite caught it; it was restored. A subsequent regression in empty-target note verification was also caught and fixed by checking for an absent or empty target note table. Two initial combined-suite invocations used the wrong working directory/PYTHONPATH and failed test-module imports; the corrected command above passed.

This prototype is qualified only on the included synthetic SQLite fixtures. It does not establish complete migration coverage for other tables, grants, unsupported note schemas, or real user data. Full-profile native conversion, packaging, and restart/reopen verification remain primary-owned.
