# Asynchronous Plugin Completion Prototype Provenance

This default-off prototype applies only when `OPENATELIER_ASYNC_PLUGIN_COMPAT=1`,
the request explicitly sets `stream` to `false`, the selected model is a
non-direct Pipe model, and validated metadata contains nonempty string
`chat_id`, `message_id`, and `session_id` values. It schedules the existing
completion and nonstream response processor as one registered task associated
with the owned chat. Other request shapes continue through the prior handler.

The task runner accepts only a normal completion dictionary with nonempty
`choices[0].message.content`. It invokes the existing response processor once.
Unsupported response shapes receive a fixed prototype error state. Exceptions
also attempt that generic state and then propagate so awaiting or stopping code
can observe the task failure. Cancellation emits the existing
`task-cancelled` event and propagates. The task done callback retrieves failures
without printing exception content and always removes the registry entry.

The first local draft placed the runner in the existing middleware module. The
primary review identified that as outside this slice's allowed placement. That
draft was removed before commit; the runner now lives in the single new
`utils/async_plugin.py` module and delegates to the existing
`process_chat_response`. A follow-up review required task exceptions to remain
observable, and cancellation at the runner's initial yield to emit the
cancellation event; both are covered by the source-extracted tests.
The pre-existing chat-metadata source-extraction test initially failed because
its namespace did not define the new opt-in selector. Its control was corrected
to explicitly select the old immediate path; no product fallback was added to
satisfy the harness.

`python3 validation/test_async_plugin_compat.py` executes six named
source-extracted tests using controlled request, task, chat, emitter, and
completion-handler stubs. These checks cover strict opt-in selection, early
acknowledgement before generation, one-time response processing, default-off
behavior, unsupported response rejection, sanitized failure state, exception
propagation, cancellation, and task cleanup. They do not validate a mounted
FastAPI application or Redis, multiple workers, request lifetime after the HTTP
response, arbitrary plugin SDKs, recovery, or general streaming behavior.
Cancellation after the runner starts is covered; a task cancelled before its
coroutine starts cannot emit an event and remains unqualified.
Primary-owned native verification is still required for those measured
journeys. The acknowledgement is accepted/running state, not a delivered
notification or completed reply.
