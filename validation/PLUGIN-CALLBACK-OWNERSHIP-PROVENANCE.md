# Plugin Callback Ownership Correction Provenance

The source69 correction changes only the existing `get_event_call` callback
factory in `backend/open_webui/socket/main.py`. Factory construction stays
lazy. When its returned coroutine is invoked, it requires nonempty string
`user_id` and `session_id` values and a current `SESSION_POOL` mapping whose
`id` equals the request user. It performs that check immediately before one
Socket.IO call and raises a fixed `ValueError` without exposing identifiers on
missing, malformed, stale, or foreign state.

`OPENATELIER_EVENT_CHANNEL_COMPAT=1` selects one `events` call. Other values
retain one `chat-events` call. The event payload and acknowledgement object are
passed through unchanged, and `get_event_caller` remains an alias. No database,
authentication lookup, session fan-out, or alternate ownership source was
added.

The bounded native observation showed the current client uses `events` for the
interactive callback. The source68 candidate instead sent the callback on
`chat-events` and accepted the foreign-session callback in the synthetic
comparison. The correction addresses the measured channel and ownership
boundary only; it does not qualify general plugin SDKs or streaming callbacks.

`python3 validation/test_plugin_callback_ownership.py` executes five named
source-extracted tests with a controlled session pool and recording transport.
They cover default and exact opt-in channels, one-call payload/result
preservation, lazy construction, owner changes between construction and
invocation, invalid or foreign session state, and alias preservation. These
tests do not validate Redis, multiple workers, session revocation/reconnect,
transport delivery, arbitrary event types, or a mounted application. The
primary owns the native six-call replay. A stale or foreign session now gives
the caller a fixed local error suitable for retry; this does not establish
human notification or consent.
