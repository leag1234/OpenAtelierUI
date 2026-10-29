# Retrieval resource access correction

A synthetic integration check reproduced an authorization defect: an ordinary nonowner called `/api/v1/retrieval/process/file` with another account's file ID, received the document text and triggered an embedding. The fixture was 161 bytes; no private data, external model or production service was involved.

The first correction resolves known file and knowledge collection IDs through persisted records before processing or querying. Missing and foreign resources receive the same generic refusal. A nullable file ACL remains private; it must not inherit the public-read meaning of a nullable knowledge ACL. Explicit supported grants and linked knowledge permissions are evaluated with the existing own ACL function. Ambiguous file/knowledge collection names fail closed for ordinary users. Administrators retain access to valid collection names.

File batches are bounded to 64, validate every resource and destination before effects, and process persisted rows rather than client-supplied file content. Invalid destinations refuse before hash, content or vector updates.

The primary nine-group regression harness executes the actual own authorization and route functions with inert storage, vector and embedding doubles. It verifies private/null behavior, explicit and linked grants, aliases/ambiguity, identical missing/foreign refusals, no forbidden effects, authorized owner query flow, all-or-nothing batch admission and canonical stored content. These checks are not actual HTTP or full retrieval qualification.

The full release gate also requires client chat descriptor authorization before filters/providers, authorized ingestion destinations and user-bound generated collection names, then actual owner/nonowner HTTP checks and successful migrated-document reindex/reuse. The first correction alone is not a usable-release or complete security claim.

The runtime correction is independently authored by the bounded no-history BSD helper from own source and the normalized synthetic observation. Primary review caught and corrected nullable-file ACL semantics, misplaced HTTP exception handling and empty batch destination admission before commit. Primary authored the tests and this record. No newer upstream implementation was supplied; no strict clean-room or independent legal clearance is claimed.


Client chat file descriptors are now validated after the existing chat-owner check and before model loading, filters or provider work. Real file/image IDs must resolve to readable persisted files. Opaque UI item IDs are not file IDs. Conflicting or malformed resource references refuse, and typed collection IDs keep their separate meaning. Nested and existing flat server-authority fields are replaced from persisted rows while unrelated integration metadata is retained. Client document content cannot override a referenced stored file. Supported inline user text is bounded to 64 KiB cumulatively and invalid Unicode refuses.

Nine further primary actual-function regression groups verify native and legacy SDK field shapes, opaque IDs, collection references, foreign and malformed denial, inline bounds, and the real chat entry stopping before model work. Initial tests exposed malformed nested data, empty/conflicting IDs, invalid collection IDs, Unicode encoding failures, and legacy flat-field authority; these were corrected before commit. Runtime HTTP proof and generated ingestion names remain open.


Generated text, web, video and search indexes now use a stable authenticated-user namespace, `oa-<32 hexadecimal owner-hash characters>-<24 hexadecimal resource-hash characters>` (60 characters). They remain opaque client identifiers. Existing file/knowledge records are resolved first, so a matching-looking name cannot override another persisted resource's ACL. Explicit destinations are authorized before page loading or vector writes. Search result URLs are checked after the authorized search returns and before page loading, then resolved document destinations are checked again before writes.

The primary eight additional regression groups verify stable ownership/domain separation, known-resource collisions, original explicit IDs, invalid input refusal, no forbidden ingestion effects, successful owner text/web flow and search loading/write boundaries. All 26 access/descriptor/namespace groups pass with inert doubles. Actual HTTP and migrated-document reindex/reuse remain required.


### Direct-provider metadata boundary

Client-supplied direct model metadata now obeys the existing direct-connection setting. Its knowledge references pass the same persisted-resource authorization before model loading or callbacks, and client local filter/action/tool bindings are refused, including nested metadata. Non-direct server presets retain their existing metadata. Optional null metadata is normalized on copies, preserving submitted objects and ordinary provider parameters. This is independently authored from the own BSD implementation; no newer upstream implementation was supplied to the author.

The primary executed 33 actual-function regression groups across retrieval, chat descriptors, owner namespaces and direct completion/callback boundaries with inert doubles (0.755 seconds). These checks do not establish HTTP qualification, migrated document reuse, complete plugin compatibility or a comprehensive security audit. The first primary callback fixture omitted an existing helper dependency and was corrected without a runtime change.
