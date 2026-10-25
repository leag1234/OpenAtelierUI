# Tool update workspace-capability gate

Recorded 2026-10-25 UTC. This is a focused source correction for the measured synthetic Tool-update admission mismatch. It is not a general security certification or permission-default decision.

## Behavior basis

The authorized behavior-only comparison used identical 82-request runs against current 0.12.4 and the old candidate. With the effective `workspace.tools` permission disabled, current rejects the ordinary Tool owner with HTTP 401 `{"detail":"401 Unauthorized"}` and no stored effect; the candidate accepted and stored the update. After enabling the permission, current allows the owner update. Revoking it again causes immediate denial in current, while the candidate again accepted and stored the update. Administrator updates pass all phases; nonowner and anonymous requests remain denied. Model-owner updates pass in all phases in both versions, so this patch changes only Tool update admission. The full controlled behavior summary is maintained outside this candidate; this provenance uses only its safe HTTP/effect description.

## Change and boundary

The existing `/api/v1/tools/id/{id}/update` route now checks the effective live `request.app.state.config.USER_PERMISSIONS` value through the existing `has_permission(user.id, "workspace.tools", ...)` helper for non-admin users. The check follows existing resource ownership/write-ACL admission and precedes import rewriting, module loading/execution, tool-cache mutation, spec extraction, and database update. A disabled capability returns the measured 401 detail. Administrators retain the existing bypass; ownership and write-ACL checks remain in place. No Model route, default permission, schema, dependency, or other endpoint changed.

The route reads the current permission mapping on each request, preserving an immediate administrator revocation path. It does not edit or broaden permissions. The capability state is directly controlled through the existing admin configuration API. Reading and use journeys are unchanged; a disabled update is visibly a 401 response, while no new UI explanation or rendered comprehension claim is made.

## Verification

`validation/test_tool_capability.py` compiles and executes the actual route function extracted from the old source with controlled dependencies. Tests cover disabled owner denial before import/module/cache/spec/store effects, enabled owner continuation, next-request revocation, administrator bypass, nonowner denial while enabled, and preservation of other permission keys. These tests do not substitute for primary-owned current/candidate HTTP replay; no Hardy runtime or external dependency was used in this worker slice.

Initial isolated command: `PYTHONPYCACHEPREFIX=/tmp/mdk-pycache python3 -m unittest -v test_tool_capability` — 5 tests passed. Primary owns the unchanged 82-request replay, live config readback, and final HTTP/effect review.
