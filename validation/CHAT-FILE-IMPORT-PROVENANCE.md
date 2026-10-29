# Chat Attachment Import Prototype

This opt-in migration path accepts the measured private-chat attachment shape only when `--include-private-chat-metadata` is selected. It verifies each `chat_file` row against the chat owner, imported file owner, user message node, and exactly one matching embedded file descriptor. Orphaned, foreign, duplicate, malformed, or ambiguous references fail before a target is created.

The target schema has no `chat_file` table. The converter retains the complete original database in its private source archive, while the prepared target represents the validated relationship through the copied chat graph and the imported file row. Source join-row IDs and timestamps remain archive-only and are reported as such. Embedded file paths are changed only in the copied chat graph to the prepared target's upload location; the source database and upload tree are not changed.

The current preview field is ID-less in the measured source profile. When the same opt-in is selected, an entry containing only `role` and `content` is mapped only if it matches exactly one unused message on the selected graph branch. The copied graph receives that node ID. Ambiguous or unmatched entries are refused. The original chat JSON remains in the archive.

This is a narrow copy-migration result, not proof of complete migration or runtime compatibility. The source profile also reports unrelated unsupported configuration and message fields; they remain visible as blockers. Independent review and a fresh application-level continuation test are required before treating a prepared target as usable.
