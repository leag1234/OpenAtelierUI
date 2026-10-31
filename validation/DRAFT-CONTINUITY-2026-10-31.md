# Chat draft continuity, 2026-10-31

The rendered field review found that an unsent new-chat draft disappeared after a page reload. The initial implementation stored drafts only from the message input used after a conversation existed; the separate empty-chat `Placeholder` input did not forward its change callback. A second race allowed the child's initial empty state to clear storage before the parent restored it.

The repair uses a small storage helper with keys scoped to both the authenticated user and the new/existing chat identity. Temporary chats never persist drafts. Ambiguous legacy keys are not read or migrated across users. Malformed values fail closed. Empty input clears the scoped draft, while attachment-only drafts remain eligible for recovery. The empty-chat and existing-chat inputs now share the same callback, and restoration completes before empty-state updates may write.

Focused pure-policy tests cover new/existing identity, user isolation, temporary-chat refusal, malformed input, attachment-only recovery and clearing. A production frontend build completed offline with the existing dependency tree. In an isolated read-only-root runtime, the synthetic text `Persistent synthetic draft` survived a real browser reload; keyboard deletion then remained empty after a second reload. No model request, plugin change, backend/schema change, private data or production effect occurred.

The build retained the baseline frontend warnings and its non-blocking offline Pyodide package-fetch warning. This qualification is agent-observed and does not replace human UX acceptance.
