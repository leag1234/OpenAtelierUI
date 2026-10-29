# Tool sharing UI prototype

The Tools editor now submits explicit read grants on create and uses the existing tool access-update endpoint when grants change. It supports private, public read access when the existing account permission allows it, named group selection through the existing group picker, and selected user IDs behind an advanced disclosure. The user-ID draft stays in the editor while public access is selected and is hidden until a non-public selection returns. It does not add write grants. If an existing grant cannot be represented by this editor, the warning keeps that state visible and omits sharing fields from ordinary tool updates, allowing the server's existing update path to preserve the current ACL.

Editing is two separate operations: tool content and metadata are saved first, then changed access grants are saved through the dedicated endpoint. If the second request fails, the editor reports that the tool details were saved while sharing was not updated. It does not claim atomicity or change tool execution behavior.

## Checks and limits

`validation/test_tool_sharing_ui.mjs` uses inert synthetic payloads and a fetch stub to check tool-scoped user/group read grant parsing, refusal of write grants, dedicated URL/body serialization, unchanged-sharing behavior, public selection with a retained hidden user-ID draft, and the partial-failure result. It does not compile or render Svelte, execute tool content, or exercise a live account/API. Public/private/group visibility, member access, revocation, and restart persistence require the primary integrated application review.
