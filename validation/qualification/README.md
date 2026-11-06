# Comparative qualification

OpenAtelierUI is **not qualified as functionally equivalent to Open WebUI**. Earlier
route, migration and consumer passes establish only their recorded finite scope.
Successful compilation, a home screen, HTTP 200 or an unsupported response cannot
establish functional parity. No finite campaign guarantees absence of defects.

## Three separate claims

1. Baseline compatibility: the pinned v0.11.3 binary and its generated public
   contract. This is the reference named by the Atlas regression report.
2. Atlas integration: baseline compatibility plus the exact unchanged consumer
   revision, configuration and representative synthetic migrated state. The
   retained October-29 consumer is not proof about an unprovided November-6 revision.
3. Current compatibility: a separately pinned current official release. On
   November 6, official release metadata reported v0.13.0. Historical v0.12.5
   results remain historical. Current runtime inventory and qualification are open.

`manifest.json` is a versioned **requirement inventory**, not a completed test suite.
Its scenario families combine roles, configurations, state, locale, theme, viewport,
file types and selected faults. Planned variants must never be reported as passes.
API operations alone omit sockets, background tasks, UI behavior and installation.
Both non-HTTP capability and configuration inventories remain explicitly incomplete.

## Test ladder and independent oracle

For each capability, record inventory, schema/routing, behavior, coherent user
journey and persistence/recovery separately. Compare the same fixture against
the official reference and candidate. Record accepted inputs, observable output,
denials, actual provider/file/database effects and post-restart continuation.
Normalize only documented nondeterminism such as IDs and timestamps. A failing
reference is an invalid positive control, never permission to waive the candidate.

`stateful_http.py` exercises a finite set of independently authored synthetic
state contracts: three roles, settings, branched conversations, ownership denial,
organization, nested folders, notes, four nonempty raw file shapes plus empty-file rejection and sharing revocation.
It also generates24 deterministic rename/pin/archive/branch/foreign-write transitions from a recorded seed, checking the independent expected state after every action. Its reopen phase requires the controller to restart the exact instance first. Use a fresh isolated dataset or a new recorded seed namespace; reusing an old namespace deliberately fails on duplicate folders.
It tests raw file storage with `process=false`, **not** text extraction, vector
search, RAG quality or arbitrary extension compatibility. HTTP evidence cannot
replace browser pointer/keyboard, accessibility or visual review.

```sh
python3 validation/qualification/stateful_http.py --base http://DISPOSABLE-LAB:8080 --output seed.json
# Controller: restart the same isolated lab instance and verify readiness.
python3 validation/qualification/stateful_http.py --base http://DISPOSABLE-LAB:8080 --reopen seed.json --output reopen.json
```

Use only isolated disposable synthetic instances. The test client does not grant
execution authority, create isolation or restart services. Preserve failed traces
and uncertain outcomes before any retry; no automatic replay of unknown effects.

Required broader campaigns include:

- ordinary sessions across models/providers, stop/retry/edit/branch, multimodal
  files, knowledge ingestion/citations and interactive output;
- local/trusted-header/API-key/pending users, ownership, groups/sharing/revocation
  and administrator settings, with positive and negative controls;
- independently documented consumers: API clients, knowledge synchronization,
  pipelines/functions and Open Terminal; license/provenance review precedes any
  actual third-party execution, and untested integrations remain unknown;
- fresh and populated migrated state, export/import, upgrade, restart, rollback,
  interruption, disk/database limits, malformed/large files, upstream errors and
  interrupted streams, checking retained work and absence of unintended effects;
- desktop/tablet/phone, English/French, light/dark, pointer and keyboard,
  accessible names/focus, rendered messages, settings and saved-work/error states;
- same-host cold/warm start, idle memory/CPU and ordinary-task latency under
  recorded budgets; regression and security/dependency audits remain release inputs.

## Mechanical gate and publication

```sh
python3 validation/qualification/gate.py --run evidence/run.json --output evidence/verdict.json \
  --candidate-digest sha256:EXACT_IMAGE_DIGEST --source-sha256 EXACT_SOURCE_ARCHIVE_SHA256
```

Exit 2 means **NOT QUALIFIED**. Unknown, skipped, unsupported, missing, failed,
wrong-build or changed evidence blocks qualification. Required routing and
behavior records cannot be replaced by screenshots or source assertions. Raw
evidence is hash-bound; the reference inventory, candidate source, image and
manifest must match. Critical/major findings block the declared scope. A changed
artifact requires requalification; a source-only local pass does not qualify an
independently rebuilt registry artifact.

The gate verifies accounting and bindings. It cannot independently prove that a
claimed oracle is honest, a trace assertion is correct, the inventory is complete
or a person understands the UI. Independent review, actual rendered review and
maintainer acceptance remain required. Add every newly found defect as a
reproducible scenario; use deliberately broken negative controls to test whether
the campaign would detect known classes of failure.

Release and nonexperimental image publication workflows now fail explicitly.
The manual validation-branch image workflow may publish only an **experimental
candidate**; it confers no compatibility verdict. Replacing the release hold must
be a reviewed change implementing build-once, qualify-that-digest, promote-that-
same-digest with preserved signed/attested evidence. Do not rebuild after the gate
and silently treat the new artifact as tested. Workflow changes here are local
until reviewed/published; they do not retroactively protect existing remote CI.

The stopping rule for a declared qualification is zero unresolved critical/major
findings, complete required evidence for that explicit scope and independent
review. A reduced supported profile must be named and accepted as reduced scope;
it must not be called global equivalence. Keep the previous service in use while
the candidate remains unqualified; production operations belong to its operator.
