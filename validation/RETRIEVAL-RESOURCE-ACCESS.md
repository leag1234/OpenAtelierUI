# Retrieval resource access correction

A synthetic integration check reproduced an authorization defect: an ordinary nonowner called `/api/v1/retrieval/process/file` with another account's file ID, received the document text and triggered an embedding. The fixture was 161 bytes; no private data, external model or production service was involved.

The first correction resolves known file and knowledge collection IDs through persisted records before processing or querying. Missing and foreign resources receive the same generic refusal. A nullable file ACL remains private; it must not inherit the public-read meaning of a nullable knowledge ACL. Explicit supported grants and linked knowledge permissions are evaluated with the existing own ACL function. Ambiguous file/knowledge collection names fail closed for ordinary users. Administrators retain access to valid collection names.

File batches are bounded to 64, validate every resource and destination before effects, and process persisted rows rather than client-supplied file content. Invalid destinations refuse before hash, content or vector updates.

The primary nine-group regression harness executes the actual own authorization and route functions with inert storage, vector and embedding doubles. It verifies private/null behavior, explicit and linked grants, aliases/ambiguity, identical missing/foreign refusals, no forbidden effects, authorized owner query flow, all-or-nothing batch admission and canonical stored content. These checks are not actual HTTP or full retrieval qualification.

The full release gate also requires client chat descriptor authorization before filters/providers, authorized ingestion destinations and user-bound generated collection names, then actual owner/nonowner HTTP checks and successful migrated-document reindex/reuse. The first correction alone is not a usable-release or complete security claim.

The runtime correction is independently authored by the bounded no-history BSD helper from own source and the normalized synthetic observation. Primary review caught and corrected nullable-file ACL semantics, misplaced HTTP exception handling and empty batch destination admission before commit. Primary authored the tests and this record. No newer upstream implementation was supplied; no strict clean-room or independent legal clearance is claimed.
