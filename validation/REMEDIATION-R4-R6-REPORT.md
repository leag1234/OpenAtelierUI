# R4/R6 notices and provenance report

Status: implemented and checked on 2026-10-31.

- `LICENSE` preserves the inherited BSD text and adds exactly one copyright line for Gaël Duval.
- `NOTICE` records the v0.6.5 baseline, pinned commit, original copyright/license, the unchanged MIT fixture and its adjacent license, plus third-party dependency/container limits.
- All 111 project-added files carrying a BSD SPDX marker have the adjacent language-appropriate 2026 Gaël Duval copyright line. Inherited baseline files were not changed for this header operation.
- `PROVENANCE.md` appends the supervisor-supplied comparison result and explicitly labels it unreplicated, textual-only and non-legal. Earlier entries are unchanged.

Checks: `LICENSE` diff is `1 insertion, 0 deletions`; the project-added SPDX scan found 111 files and zero missing copyright lines; `git diff --check` passed.

This remediation does not constitute an independent legal review or a complete dependency/image license audit. The procedure in `audit/PROVENANCE-AUDIT.md` remains required before a public clean-room or provenance claim.
