# Direct resource current-version metadata

Recorded 2026-10-25 UTC. This is an opt-in metadata bridge for the isolated imported-history prototype, not a history write path or durable storage decision.

## Observed behavior

The authorized current 0.12.4 comparison exercised 27 direct Tool and Model GET requests. Authorized owners, administrators, and read-granted callers received the exact current imported version UUID while existing private Tool content and Model parameters remained redacted. Unrelated and anonymous callers remained denied for private resources. The candidate's direct resource responses did not expose the current version pointer.

## Change and boundary

Direct `GET /api/v1/tools/id/{id}` and `GET /api/v1/models/model?id=...` now include `version_id`. After the existing resource lookup and read authorization, each route applies its existing content or parameter redaction before consulting the imported-history helper. The helper uses the existing opt-in sidecar and freshness validation, returns only its verified manifest pointer, and confirms that the pointer names a history row for that resource without decoding or returning a snapshot.

When imported history is disabled, absent, stale, or unavailable, a permitted active-resource GET continues to return its normal useful response with explicit `version_id: null`. This does not create revisions or claim that later edits are recorded. The pointer is metadata; it does not grant history-detail access, and the existing history route authorization remains unchanged. No list/catalog/create/update response, schema, default, dependency, or other API changed.

## Verification and limits

`validation/test_version_metadata.py` extracts and executes the actual two route functions with controlled dependencies. It checks pointer projection, private denial before the metadata reader, Tool content and Model parameter redaction before lookup, administrator behavior, and null fallback for disabled/missing/stale history. `validation/test_imported_history.py` additionally checks exact pointer lookup against the isolated imported fixture, disabled opt-in refusal, and stale pointer refusal without snapshot reads.

These source-isolated tests do not replace the primary-owned current/candidate HTTP replay or confirm rendered client comprehension. Runtime history remains opt-in and limited to the validated synthetic sidecar. History recording, newer edits, list/catalog metadata, full migration eligibility, performance targets, and durable architecture adoption remain outside this slice.
