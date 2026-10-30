# OpenAtelierUI portable development delivery — 2026-10-29

Own application runtime and compiled frontend: `bf502f34143a4a7f919860afd6444be4cac9773b`. Linux/amd64 image config ID: `sha256:205b616dcf0da4687c83d30a5e83ef8cdca5fbc7e8532f3f850ce3dd3305c9f4`.

The separately supplied `openatelierui-portable-own-images-03.tar.gz` archive is 1,798,267,156 bytes with SHA-256 `cbcd9a62bf72dee503476798731fb6abd1f552024d33acbea83e872e98d0a52b`. It contains the own core and retained dependency base, without external consumer source, private data, credentials or model weights. It is kept outside Git and has not been uploaded to a registry. Verify exact bytes and checksum before loading it into a selected development engine:

```sh
sha256sum openatelierui-portable-own-images-03.tar.gz
docker load --input openatelierui-portable-own-images-03.tar.gz
docker image inspect --format '{{.Id}}' sha256:205b616dcf0da4687c83d30a5e83ef8cdca5fbc7e8532f3f850ce3dd3305c9f4
docker tag sha256:205b616dcf0da4687c83d30a5e83ef8cdca5fbc7e8532f3f850ce3dd3305c9f4 openatelierui:development-20261029
```

Use the [copy migration and lifecycle guide](OPENATELIERUI-INSTANCE-PROVENANCE.md) to preview a closed source, prepare a supported new target, then start/status/stop/restart using that target. The helper requires a populated source account and a separately configured operator environment. It does not create a first administrator, install an engine, change a host network or switch a production instance.

The [qualification record](RELEASE-QUALIFICATION-20261029.md) binds the actual desktop/mobile composer, keyboard, saved-chat and selected unchanged-consumer workflows. It distinguishes real tool execution, real local-model responses and deterministic transport. The [migration inventory](MIGRATION-READINESS-INVENTORY.md) retains unsupported and archive-only domains. Broader modern-version parity, private-data cutover and production recovery remain unqualified.

The 2026-10-30 continuation qualified the later pinned consumer source `dcd49b29be059c48482c1173d33ad64cfd5e85ec` with its 54 packaging/plugin inputs unchanged. The saved project conversation reopened, its ten-step disclosure remained available, and agent review found the project/navigation surfaces usable at desktop and 375×812. It also ran a converted synthetic version-0.11.3 account, saved chat and upload before and after a controlled restart with owner/foreign isolation checks. These additions do not change the image ID, activate a migrated target, establish human UX acceptance or assert general future-version parity.

The own application is BSD-3-Clause. Complete images and dependencies retain their own licenses; read [provenance](../PROVENANCE.md) and the [independent audit procedure](../audit/PROVENANCE-AUDIT.md). The optional foreground launcher remains a separate prototype and does not qualify direct host installation.

Retain a system-disk reserve for engine-internal import/build scratch even when the archive is on a large data volume. After completed jobs, clean only verified inactive task intermediates, preserving source originals, rollback inputs, current deliverables and sufficient failure evidence.
