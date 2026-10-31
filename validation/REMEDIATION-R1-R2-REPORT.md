# R1/R2 service and publication-target report

Status: implemented and source-tested on 2026-10-31.

- Legacy `CUSTOM_NAME` remains accepted but performs no request; operators use `WEBUI_NAME` and `WEBUI_FAVICON_URL` directly.
- Inherited license-service retrieval and its startup call were removed. `LICENSE_KEY` remains an inert compatibility setting.
- Release checking is disabled by default and requires `OPENATELIERUI_ENABLE_RELEASE_CHECK=true`; its optional target is this repository.
- The default favicon URL is empty and configurable rather than an inherited host.
- OpenRouter attribution headers, search user agent and visible help/release links identify this project.
- Compose and Kubernetes use locally supplied `openatelierui` images and refuse implicit pulls.
- The inherited Hugging Face deployment workflow was removed. Issue and pull-request templates now target this project.

Verification:

- Source scan found no `api.openwebui.com`, `openwebui.com`, `open-webui/open-webui`, `ghcr.io/open-webui`, `docs.openwebui.com` or `helm.openwebui.com` runtime/deployment target under `backend`, `src`, `Dockerfile`, Compose, Kubernetes or `.github`.
- `validation.test_provenance_remediation` and `validation.test_ui_identity_sources` passed.
- The optional release-check flag is off by default. No network request was made by the tests.

Compatibility: inherited API routes, Python import namespace, Compose volume and Kubernetes resource names remain unchanged. Local image tags must be built or loaded before deployment; this is intentional and prevents an accidental pull from another project.
