# Model sharing UI prototype

This change updates the model editor to send the explicit read-grant shape already accepted by the local model API. It supports private access, public read access when the existing account permission allows it, named group selection through the existing group picker, and additional selected user IDs behind an advanced disclosure. That user-ID input is hidden while Public is selected; its draft remains in the editor if visibility changes before saving. It does not grant write access. If an existing grant has a shape this editor cannot represent safely, the editor reports that and omits sharing fields from ordinary detail updates so the saved ACL is preserved.

Model creation sends `access_grants` with the model form. Editing saves model details first and calls the existing `/models/model/access/update` endpoint only when the read grants changed. These are separate server operations: if the details save succeeds and the sharing update fails, the page remains open and reports that the details were saved while sharing was not. This is not an atomic update.

The model listing now uses “Author unavailable” when the response omits its user object; it does not request or expose another profile field. The change does not alter server authorization or the server's grant conversion.

## Checks and limits

`validation/test_model_sharing_ui.mjs` runs with the bundled Node runtime and checks user/group read grant parsing and serialization, refusal of write/ambiguous grants, user-ID validation, create payload filtering, edit request ordering, unchanged-grant behavior, and the partial-save failure state.

The source test does not compile or render the Svelte interface and does not exercise a live account or model API. Public/member visibility, revoke, restart persistence, keyboard behavior, and visual hierarchy require the primary integrated application review. The Tools editor remains outside this change; its legacy `access_control` submission needs a separate review against its backend form contract.
