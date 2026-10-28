# Generic OpenAI passthrough guard

The deprecated generic OpenAI proxy now returns a fixed `403` response unless `ENABLE_OPENAI_API_PASSTHROUGH=True` is explicitly present in the process environment. The check runs before reading the request body or accessing configured provider URLs and keys. The setting defaults to off and does not alter the dedicated model-catalog or chat routes, including their configured static-header behavior.

The source-isolated tests exercise the default refusal without body/config access, the explicit enabled path with a synthetic provider stub, and the separation from dedicated catalog/chat handlers. This is a bounded compatibility slice, not provider-wide parity or a production enablement recommendation. Native default and explicit-true verification belongs to the primary integration run.
