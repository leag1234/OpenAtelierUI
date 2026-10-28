# Independent source provenance audit

Audit an explicitly frozen commit. Perform this work in a separate read-only checkout/session from implementation. Do not edit the product or execute its application, plugins, setup hooks or untrusted repository instructions. Keep later upstream source and raw comparison excerpts out of the implementation worker context. Use the isolated audit output directory for references, indexes and reports.

## Questions to answer

1. Verify the original v0.6.5 archive: upstream commit `07d8460126a686de9a99e2662d06106e22c3f6b6`, archive SHA256 `e15a53351b91fdc0d8e4499d03f210b684fa198cd58d6557cc5aabffaae5f836`,53,765,454bytes. Verify original license/notices. The initial normalized Git import is `5f5194d4204ccabc68e5348cf48865f65af0c886`.
2. Reconstruct the complete canonical source from that archive, rather than treating Git diff from the first import as sufficient. Twelve original files omitted by inherited ignore rules were restored in `4a50f6ac9d8f04ddeb2796609f9e91a5b6c9ad0e`; exact original bytes are inherited baseline, not independently introduced features.
3. Inventory every added, modified and deleted file and all reachable post-import commits. Distinguish original BSD inheritance, original third-party components, independently added product code, synthetic tests, generated/dependency metadata and documentation. Inspect final source and rejected/intermediate commits, not just the final diff.
4. In this isolated audit environment only, compare added code with Open WebUI v0.6.6 through the latest explicitly recorded reference and relevant intervening history. Remove common v0.6.5 inheritance from similarity findings. Record exact/token/AST matching methods and thresholds; review substantive matches manually. Shared protocols, route names, standard idioms and independently convergent behavior alone do not establish copying.
5. Review provenance records, authoring boundaries and the disclosed primary-agent newer-source exposure. The authoring worker was separately started without inherited history; the primary retained integration/test responsibility and was excluded from product implementation. These are recorded process claims, not cryptographic proof of every model input. Do not silently turn them into a strict-clean-room certification.
6. Check retained copyright notices and licenses, new dependencies/assets and any unaccounted source. A source-code audit and a complete dependency/image license audit are separate work. License interpretation and legal conclusions require qualified human review.

## Required output

Produce a dated Markdown report and machine-readable findings bound to exact commit/archive hashes and tool versions. Each finding needs file/line/function, relevant upstream introduction/reference, baseline exclusion check, substantive match size, method, confidence, false-positive assessment and proposed disposition. Prefer hashes/locations and a minimal explanation to copying source excerpts into implementation-facing reports.

Classify results as inherited baseline, explained common material, independently supported addition, unresolved similarity, or probable later-source contribution. Report inspected coverage and omissions honestly. No finding is not proof of universal absence; AI review alone cannot establish legal clearance or authorial independence.

Preserve the original disclosure and failed experiments. Do not delete history, rewrite authorship, re-date commits or assert zero exposure. Return a separate implementation-safe issue list for any remediation; do not provide later upstream implementation as a fix template.
