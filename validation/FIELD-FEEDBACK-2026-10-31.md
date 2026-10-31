# Field replacement feedback, 2026-10-31

The maintainer supplied results from a disposable, offline hardened-container replacement rehearsal. This report records those results as external field evidence and maps each actionable observation to the current private review branch. It does not import third-party instructions or source.

## Reported measurements

| Measurement | Lightweight OpenAtelierUI | Existing backend |
|---|---:|---:|
| Idle memory | 246 MiB | approximately 531 MiB |
| Ready time | 19 seconds | 15 seconds in the VM |
| Idle CPU | 0.1% of one core | less than 1.2% |
| Disk footprint | 1.6 GiB | 3.1 GiB |

Conditions reported by the maintainer: disposable Hardy container, no network, read-only root filesystem, 1 GiB memory limit, and optional heavy packages such as PyTorch, Transformers, OpenCV and Playwright removed. The 246 MiB result is therefore promising evidence for the lightweight profile, not a universal benchmark or a comparison of identical dependency sets.

## Actionable observations

1. `backend/start.sh` now appends operator arguments to Uvicorn as quoted, separate argv values. An executable regression uses a fake Uvicorn process and verifies exact token preservation.
2. The implicit SQLite URL remains `DATA_DIR/webui.db`. When that implicit data directory cannot be created or written, startup now raises one actionable OpenAtelierUI error naming the `DATA_DIR` and `DATABASE_URL` remedies. An explicitly supplied database URL keeps its existing behavior.
3. Static hydration is centralized. A pre-populated read-only `STATIC_DIR` is used without copy attempts or error-log flooding. A missing/uncreatable target emits one bounded warning, while a writable tree retains the existing relative layout.
4. The previously reported old banner is already corrected: startup displays `OpenAtelierUI`, the package version and this project's repository URL.

The package metadata also now uses the BSD license classifier rather than the stale inherited MIT classifier. The `openatelierui` console command is available, while `open-webui` remains as a compatibility alias.

The delivered Dockerfile now embeds `LICENSE`, `NOTICE` and `PROVENANCE.md` and overrides inherited OCI title, source, URL, license, version and revision labels with OpenAtelierUI-specific values. A private offline rebuild verified those labels on the resulting image.

## External consumer mismatch

The report says an external conversation wrapper awaits a value that this baseline returns synchronously, and that a consumer-side adapter accepting both forms has been prepared. The named wrapper source is not present in this checkout or the public default branch searched on 2026-10-31. No product change is guessed from that description. The existing HTTP routes and wire contracts remain unchanged; the consumer adapter must be reviewed from its exact source before publication or integration.

## Qualification boundary

A private offline image from exact commit `fd8f2f9d8f4b36db381ed573b8cc3f20f8ba7079` subsequently passed hardened read-only startup, forwarded arguments, the explicit unwritable-data refusal, normal restart, and actual 1600x1000 and 375x812 selector review. That full compatibility image sampled approximately 510 MiB at idle and has a 5.00 GB virtual size; it is not the reported lightweight 246 MiB/1.6 GiB profile. The rendered review also found two non-blocking usability gaps: suggested prompts remained English in a French interface, and an unsent draft was not restored after reload.

The complete external project/document/save/existing-name-refusal journey and exact consumer wrapper review remain release-qualification work. No production deployment, public issue, consumer modification or merge is authorized by this report.
