# OpenAtelierUI development qualification — 2026-10-29

This is a development review record, not production, legal or complete compatibility acceptance. The implementation starts from the retained BSD-3-Clause v0.6.5 baseline. The exposure incident and separated implementation limits in [PROVENANCE.md](../PROVENANCE.md) still apply.

## Measured behavior

- Actual Socket.IO enrollment and explicit session revocation passed on implementation `fb72162122a24f899a48a351950d0b838a2ad8a7`: anonymous, invalid and stale sessions received no private user identifiers; authenticated late join worked; revocation disconnected both target-user sockets while another administrator remained connected; a fresh sign-in reconnected. This is a single-worker qualification. See [realtime provenance](REALTIME-IDENTITY-PROVENANCE.md).
- A selected synthetic imported OpenAI connection passed nine native authentication, catalog, selected-model, static-header, disabled-model and default-proxy checks. Selected bounded configuration now prepares for independent review without the former unconditional prototype blocker. Unknown configuration and unsupported domains still refuse or report blockers.
- A synthetic conversion retained all canonical typed rows, schemas and uploaded bytes compared with the frozen earlier conversion. Newly created target/archive directories were mode 0700 and files 0600; original hashes and modes were unchanged. This does not authorize activation or private-data cutover.
- Frontend `1ffb20322b5b4a9c4d091f6eb26d219baeec774f` was compiled and inspected at desktop and narrow viewports. Normal sign-in profile hydration, collapsed navigation, mobile focus entry, Tab containment, Escape and direct reopening were checked. This is agent visual review, not full accessibility, feature or human UX acceptance.
- An unchanged external consumer plugin set loaded over the own core. Actual project UI document selection produced a factual local-model response; the saved response returned after a four-service restart, a follow-up completed, and both responses returned after page reload. This journey required a separately disclosed external request-schema correction, outside this BSD repository. It does not establish that every external stack works without configuration or upstream corrections.
- Operator implementation `ad629db2e74625b55b52068793426e0721e6ef04` passed 21 primary-run lifecycle/coordinator tests, including exact conversion-target handoff, source/upload preservation and loopback URL receipts. These lifecycle tests use mocked Docker, not an actual named-network installation. Follow the [operator guide](OPENATELIERUI-INSTANCE-PROVENANCE.md).

## Remaining limits

General modern-version parity is incomplete. Multi-worker/Redis behavior, all migration domains, arbitrary plugins, groups and rights, external providers, connector/OAuth execution and production recovery are not qualified by these selected tests. Preview blockers remain authoritative; do not treat archived unsupported data as active migrated functionality.

The isolated integration used extracted document text while embedding service configuration was absent; the interface explicitly reported partial coverage. One short follow-up hit the laboratory's 48-token ceiling and included a truncated coverage phrase. These are not general model-quality results.

An external laboratory model library selected an unsupported AVX instruction on an old CPU. A documented per-process SSE4.2 restriction passed 14 numerical cases and the fresh project journey. The failed request was preserved and not replayed. This is a laboratory hardware qualification, not an OpenAtelierUI numerical change or a performance improvement claim.

Internal Docker networks were reachable directly from the host in a separate small probe, but published loopback forwarding failed on two lab hosts. The generic operator's published-port path therefore remains unqualified for that network profile; no firewall, daemon or isolation change was made to hide the failure.

No general performance gain, production readiness, formal clean-room separation or independent legal clearance is asserted. Third-party dependencies and complete images retain their own license obligations. Original data and failed evidence remain preserved in the parent validation workspace.
