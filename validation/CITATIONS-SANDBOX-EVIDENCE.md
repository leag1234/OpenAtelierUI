# Citations modal passive HTML preview

The citations modal in `src/lib/components/chat/Messages/CitationsModal.svelte` passed citation HTML through `srcdoc` with sandbox privileges `allow-scripts allow-forms allow-same-origin`. This bounded old-BSD change sets the same iframe to `sandbox=""`, removing those opt-ins and applying the browser's default sandbox restrictions. It leaves `srcdoc={document.document}` unchanged, preserves the source link/open-file control and title, and leaves non-HTML citation content on the existing `<pre>` path.

The HTML branch now labels the content as a static preview and states that scripts and forms are disabled. Ordinary text and source selection remain available; interactive HTML behaviors that depend on scripts/forms or same-origin access are intentionally unavailable. The existing close control, source display, and direct open-original-file route remain in the source. No source bytes are rewritten.

`python3 validation/test_citations_modal_sandbox.py` runs three named stdlib source checks for the empty sandbox and unchanged srcdoc expression, notice, source/open-file control, and non-HTML path. This does not compile Svelte or qualify the application. The primary owns the browser primitive control using the exact old value `allow-scripts allow-forms allow-same-origin`, the candidate value empty, and a harmless synthetic fixture; that test must show parent/DOM mutation blocked while static text remains visible. No browser result is claimed here.

This only changes the citations modal. The separate `Artifacts.svelte` configurable `allow-same-origin` behavior remains outside this lot. No interactive-artifact changes, frontend build, comprehensive XSS remediation, or production security claim is made.
