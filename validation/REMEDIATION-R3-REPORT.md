# R3 identity and visible-flow report

Status: implemented, source-tested and production-compiled on 2026-10-31.

- Product name: `OpenAtelierUI`; distribution name: `openatelierui`.
- Python import namespace `open_webui` is retained for integration and plugin compatibility.
- Custom `WEBUI_NAME` values remain supported and no upstream suffix is appended.
- Inherited community-export/share flows and enterprise-license banners were removed. Local chat-link sharing remains available.
- Inherited model/community links and remote social badges were removed.
- `demo.gif` and the inherited Hugging Face workflow reference were removed.
- Original upstream copyright attribution remains visible in About and in repository notices.

Verification:

- No visible `Open WebUI` string remains under `src` outside the About attribution and inherited translation catalogs.
- Thirteen identity/provenance source tests passed in the first focused run; the final combined source suite also passed.
- A production Vite build on Hardy completed successfully: 4,545 modules transformed and the static site written to `build`.
- Global `svelte-check` is not a clean gate for this baseline: it reports 5,210 pre-existing errors across 232 files. Vite compilation succeeded and found no remediation syntax/import error.

The build used an isolated task directory on Hardy's RAID, an existing dependency tree and a temporary Node runtime copied from Fire. No dependency was downloaded or installed system-wide.
