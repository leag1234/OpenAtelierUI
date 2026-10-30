# Release-candidate checklist

This checklist is consumer-neutral. Passing it does not authorize a production deployment, private-data cutover, merge or public release.

## Identity and provenance

- [ ] Source commit, BSD baseline and image identity are pinned.
- [ ] `LICENSE`, `PROVENANCE.md` and `OPENATELIER-SOURCE.json` are present.
- [ ] The independent provenance dossier matches the candidate commit.
- [ ] No customer, deployment or external-consumer branding appears in release presentation or fixtures.

## Installation and recovery

- [ ] Start, status, stop and restart succeed from an extracted package.
- [ ] The operator can identify the loopback URL and current state without reading logs.
- [ ] Original data, converted copy and stopped candidate workspace are retained independently.
- [ ] Rollback returns to the previous installation without writing candidate data over it.
- [ ] Interrupted or failed workspaces are clearly distinguishable from usable ones.

## Migration

- [ ] Preview creates no target.
- [ ] Conversion writes only to a new private target.
- [ ] Accounts, conversations, files, notes and selected configuration reopen after restart.
- [ ] Unsupported rights and configuration are refused or explicitly archived.
- [ ] Source database, sidecars and uploads remain byte-exact.

## Compatibility and security

- [ ] Existing plugin and integration inputs remain unchanged.
- [ ] JSON and streaming completion paths pass with authentication and cancellation.
- [ ] User, group, file, note, model and tool isolation checks pass.
- [ ] Imported keys and secrets are never printed in receipts or package metadata.
- [ ] HTML/file responses retain the qualified sandbox and content-type protections.

## Interface and resources

- [ ] Desktop and narrow viewports have readable hierarchy, one usable composer and no observed horizontal overlap.
- [ ] Keyboard/direct controls, saved-work return and error states are reviewed on the rendered application.
- [ ] Startup time, idle memory, task latency and retained disk use are measured against the pinned reference profile.
- [ ] Build, test and package scratch is removed while required rollback and diagnostic evidence is retained.

## Release decision

- [ ] Automated checks are terminal and successful on the exact candidate.
- [ ] Remaining optional or unqualified features are listed in release notes.
- [ ] Human provenance, UX and deployment reviews are recorded separately.
- [ ] No passing laboratory experiment is presented as automatic production acceptance.
