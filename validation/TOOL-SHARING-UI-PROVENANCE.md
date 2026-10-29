# Tool sharing UI prototype

The Tools editor now submits explicit read grants on create and uses the existing tool access-update endpoint when grants change. It supports private, public read access when the existing account permission allows it, and selected user IDs. It does not add group or write grants. If an existing grant cannot be represented by this editor, the warning keeps that state visible and omits sharing fields from ordinary tool updates, allowing the server's existing update path to preserve the current ACL.

Editing is two separate operations: tool content and metadata are saved first, then changed access grants are saved through the dedicated endpoint. If the second request fails, the editor reports that the tool details were saved while sharing was not updated. It does not claim atomicity or change tool execution behavior.

## Checks and limits

`validation/test_tool_sharing_ui.mjs` uses inert synthetic payloads and a fetch stub to check tool-scoped grant parsing, refusal of group/write grants, dedicated URL/body serialization, unchanged-sharing behavior, and the partial-failure result. It does not compile or render Svelte, execute tool content, or exercise a live account/API. Public/private visibility, member access, revocation, and restart persistence require the primary integrated application review.
