# Behavior-only first implementation slice

Status: prepared for a genuinely non-exposed, explicitly authorized implementation worker; not dispatched. This document contains observable requirements and old BSD base identity, not newer upstream implementation or patch excerpts. The primary agent's provenance incident remains separately disclosed and cannot be erased by a new chat with inherited history.

## Permitted inputs and boundary

- Exact BSD source baseline: Open WebUI v0.6.5 commit `07d8460126a686de9a99e2662d06106e22c3f6b6`, imported in the independent local repository `work/openatelier/candidate` as `5f5194d4204ccabc68e5348cf48865f65af0c886`.
- Independently authored client and fixture code under `experiments/openatelier/clients/`, plus resulting behavior-only summaries. Selected third-party Input Inspector fixture is unchanged MIT code with its notice; it is a test input, not BSD-owned code.
- Existing root AGENTS/CONTRIBUTING authority constraints and the bounded task contract apply. No private data, external AI/provider, host configuration, new trust, publication, merge or durable default/architecture selection is authorized.
- Do not read/search newer Open WebUI implementation, patches, migrations, tests, translations, mirrored repositories or the parent conversation. Do not use broad web/plugin searches. Public API requirements below are enough for this slice.
- Write only independently authored BSD-3-Clause code, narrow tests and a provenance entry. Preserve old notices and the immutable import commit. No conflicting writer; primary owns black-box validation and integration review.

## Required observable changes

1. `POST /api/v1/chat/completions` must invoke the same certified chat path as `POST /api/chat/completions`: identical auth, payload handling, errors, model visibility and plain/SSE behavior. On the frozen old baseline the alias returns405; on the actual v0.12.3 reference it succeeds with the same portable plugin. Do not invent a parallel code path or weaken permissions.
2. `GET /api/v1/functions/list` must return the functions JSON list under the appropriate existing authentication/visibility rules. On the old baseline it currently falls through to HTML with status200; the actual reference returns a JSON list. A200 status alone is not success. Test unauthenticated and ordinary-user behavior without escalating permissions.
3. Demonstrate a provisional compatibility mechanism for the independently authored internal-user fixtures **without editing their source bytes**. A synchronous pipe calls `Users.get_user_by_id(user_id)` and reads the result's email; a current asynchronous pipe awaits that same accessor and reads email. The old baseline supports the former and returns an await error for the latter; the actual recent reference supports the latter and returns a coroutine-attribute error for the former. Existing native old-base callers must keep working. Preserve `None`/not-found and exception behavior, avoid blocking the server event loop and document exactly which accessor/result types are covered. This is a bounded experiment, not a blanket claim for every internal symbol.

Use the provided independent fixtures as the acceptance oracle; both must produce the expected synthetic email in plain and streamed responses. Do not write a test that merely checks implementation syntax or invent a source-modified plugin to make it pass. The unchanged MIT Input Inspector must still execute, branch/file readbacks must remain intact, and unauthorized plugin creation/file access must remain refused.

## Scope exclusions and follow-on work

Do not implement an entire migration engine, rewrite UI, select a durable plugin-isolation architecture, copy the current schema into source, or claim security remediation. Observed current and old synthetic databases have48 and23 tables; direct old-runtime opening of a copied current database fails on duplicate `share_id`. A separate independently implemented importer must address normalized messages, grants/config/history and recovery; no simple volume-swap instruction is acceptable.

First implementation output: narrow candidate diff, independently authored tests, provenance log, source hashes and honest limitations. Primary then runs the exact same isolated synthetic clients on Hardy and retains before/after receipts. If a mechanism needs a durable interface/architecture decision, leave it as an experimental proposal for review rather than silently accepting it.
