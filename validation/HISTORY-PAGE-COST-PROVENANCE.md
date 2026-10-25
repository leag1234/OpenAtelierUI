# Request-local history page author lookup reuse

Recorded 2026-10-25 UTC. This source change targets repeated author lookups measured in the bounded imported-history reader. It does not claim that repeated lookups caused the full HTTP latency miss.

## Change

`list_history` now creates a local map for the rows in one already-authorized page and resolves each distinct author ID once. A missing user is cached as `None` only for that page. The map is discarded when the call returns; there is no cross-request state, extra dependency, permission change, schema change, or history write. Detail reads and all other response projection behavior remain unchanged.

## Verification

The isolated history tests verify that a twenty-row single-author page invokes its lookup once; a mixed-author page invokes at most one lookup for each distinct ID, including a missing user; projected names and `None` values remain correct; and a rename is visible on the next request. The broader candidate suite exercises the existing history reader and route authorization paths.

Primary owns the frozen actual HTTP replay and timing/call-count comparison. The pre-change page median was 12.14 ms and p95 28.80 ms against a provisional p95 target of 20 ms. Added idle memory remains unmeasured. No latency, resource, energy, or general performance gain is claimed by this source patch alone.
