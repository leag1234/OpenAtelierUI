# Chat session compatibility prototype provenance

This bounded source70 prototype joins the already-supported Pipe task lifecycle to the bundled chat UI. It preserves the existing completion handler and response processor; for an opted-in streaming Pipe response it reshapes only the task acknowledgement already returned by that processor. It does not create a second task. The non-stream Pipe path continues to use its existing task runner.

The response-only `/api/config` field `event_call_channel` selects one socket channel: `events` only when `OPENATELIER_EVENT_CHANNEL_COMPAT` is exactly `1`, and `chat-events` otherwise. The UI binds and removes the selected listener on socket/config changes, while the layout's existing session-ID check remains in place. Task acknowledgements accept the current `task_ids` list and legacy single `task_id`, discard invalid entries, and deduplicate IDs.

Saved chats stop through the owner-scoped per-chat endpoint. The UI clears task IDs and marks response branches complete only after a successful stop, or after a false stop followed by an owner-scoped empty task list. A failed or ambiguous result leaves the state available for another stop attempt. Temporary chats and the existing `local` / `local:` ephemeral identifiers retain the prior task-ID stop path. Late completion errors, acknowledgements, stop replies, and task-list replies are ignored after navigation to a different chat. An acknowledgement arriving after the matching response is already marked done does not restore a stale Stop state.

The helper is plain JavaScript (`src/lib/utils/chat-session.js`) so its channel, acknowledgement, navigation, ephemeral-ID, encoded-path, and stop-disposition behavior can be exercised directly with the available Node runtime. This is source and component-level verification; it does not establish a compiled frontend, rendered browser result, or current-version product parity. The primary worker owns those checks. Live socket reconnect behavior, broader task recovery, and UI visual/accessibility acceptance remain unqualified.

Focused checks:

- `python3 validation/test_async_plugin_compat.py`
- `python3 validation/test_chat_session_compat.py`

Both are source-extracted or helper-level checks. The mounted backend/API, native task scheduling, compiled frontend, and browser stop/reopen journeys require the primary worker's independent verification.

Source71 repair: the primary's offline build05 stopped normally at `+layout.svelte` because three newly added variable declarations used TypeScript annotations inside the existing plain `<script>` block. Those annotations were removed while retaining the same initial values and behavior. The source-extracted metadata test also omitted the already-imported `StreamingResponse` symbol from its isolated namespace; the test now supplies a harmless binding, with its assertions unchanged. The failed build receipt is retained by the primary. This repair does not establish that the next frontend build or browser journey succeeds.
