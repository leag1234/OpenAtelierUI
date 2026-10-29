# OpenAtelierUI isolated instance lifecycle helper

The canonical operator tool is the standard-library `validation/openatelierui_instance.py`. Its old `validation/atlas_instance.py` path remains a tiny command/API alias. Example command shape:

```text
python3 validation/openatelierui_instance.py start --image openatelierui-candidate:local --expected-image-id sha256:<64-lowercase-hex> --source-data /path/to/closed-copy/data --env-file /path/to/operator.env --workspace /path/to/new-private-workspace --network openatelierui-private --host-port 18080 --container-port 8080
python3 validation/openatelierui_instance.py status --workspace /path/to/new-private-workspace
python3 validation/openatelierui_instance.py stop --workspace /path/to/new-private-workspace
python3 validation/openatelierui_instance.py restart --workspace /path/to/new-private-workspace
```

The tool requires the invoking operator to run as UID/GID 1000, an existing local image tag that matches the supplied image config ID, and a pre-existing named runtime network. Docker calls use the verified config ID and `--pull=never`; no network is created by the helper. A normal guest-only bridge and actual published loopback lifecycle were qualified inside a no-NIC VM; the operator's production network and egress policy remain unqualified. The host port binds only to loopback; the container listen address and port are set explicitly.

`start` refuses an existing workspace, source tree symlinks, SQLite sidecars, a missing/non-regular `webui.db`, an env file not regular and mode 0600, oversized input or an image mismatch. The new workspace and its manifest are mode 0700/0600. Data and retained pre-session copies use mode 0700 directories and 0600 files. Static directories use 0755 and files 0644 so UID/GID 1000 can read and write the mounted static tree. Source data is hashed before and after copy; the copied tree is compared before changing the new database. The env-file path is retained privately; the env-file contents are never read or emitted.

The tool reads `/app/backend/open_webui/static` and `/app/build/static` from a temporary never-started container made from the pinned image. It merges the first tree and overlays the frontend static tree into a fresh static directory. It retains the copied original DB under `pre-session/`, invokes the packaged `session_epochs.py prepare-copy` against that retained file in a network-none resource-bounded container, then replaces only the new data copy's `webui.db`. Startup flags enable the qualified session, private-notes, imported-profile/history and lazy-local-embedding slices, while disabling signup, title/tag/autocomplete, follow-up, version-check and model auto-update settings. Provider and trusted-header configuration remains supplied by the operator env file.

The app container is non-root (`1000:1000`), read-only root, all capabilities dropped, no-new-privileges, 256 pids, 2 CPUs, 2 GiB memory and swap limit, and a 64 MiB `nodev,noexec,nosuid` `/tmp`. Only the explicit data/static binds are writable. Health polling is bounded to 240 seconds and bypasses ambient HTTP proxy configuration. On failure, the helper attempts a normal stop only after checking its random instance label, pinned image ID and workspace mounts; copies remain for diagnosis. `status`, `stop` and `restart` repeat those identity/mount checks. Successful `start`, `status`, and `restart` receipts include the loopback URL derived from the recorded host port. A stop that cannot be confirmed is recorded as `stop_not_confirmed` when the private manifest remains writable.

## Start from a supported SQLite copy

This copy-only path expects an existing closed source data directory containing `webui.db` and `uploads/`, plus a compatible empty target template and a populated account that can sign in. The preview command creates an inspection workspace but no application target. Inspect its JSON result and proceed only when it reports `preview_ready`; a partial or refused preview is not approval to continue. The example selects the bounded migration domains validated by this prototype. Remove a selector only when intentionally leaving that domain archived, then inspect the new preview again.

```sh
SOURCE_DATA=/path/to/closed-source-data
SOURCE_DB="$SOURCE_DATA/webui.db"
UPLOADS="$SOURCE_DATA/uploads"
TEMPLATE=/path/to/empty-old-target-template.sqlite
PREVIEW_WORKSPACE=/path/to/new-preview-workspace
CONVERT_WORKSPACE=/path/to/new-convert-workspace
TARGET=/path/to/new-converted-data
IMAGE_TAG=openatelierui-candidate:local
IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$IMAGE_TAG")"

python3 validation/migrate_atlas_copy.py preview \
  --source-db "$SOURCE_DB" --uploads-root "$UPLOADS" \
  --empty-old-target-template "$TEMPLATE" \
  --workspace-dir "$PREVIEW_WORKSPACE" \
  --include-private-chat-metadata --include-named-tool-configuration \
  --include-user-profile-fields --include-api-keys \
  --include-api-key-configuration --include-openai-configuration \
  --include-user-permissions --include-identity-configuration --include-function-history

# Run convert only after reviewing a preview_ready result.
python3 validation/migrate_atlas_copy.py convert \
  --source-db "$SOURCE_DB" --uploads-root "$UPLOADS" \
  --empty-old-target-template "$TEMPLATE" \
  --workspace-dir "$CONVERT_WORKSPACE" --new-target-dir "$TARGET" \
  --include-private-chat-metadata --include-named-tool-configuration \
  --include-user-profile-fields --include-api-keys \
  --include-api-key-configuration --include-openai-configuration \
  --include-user-permissions --include-identity-configuration --include-function-history \
  --include-history-sidecar

# Use the target directory itself: it contains webui.db and uploads/.
python3 validation/openatelierui_instance.py start \
  --image "$IMAGE_TAG" --expected-image-id "$IMAGE_ID" \
  --source-data "$TARGET" --env-file /path/to/operator.env \
  --workspace /path/to/new-instance-workspace \
  --network openatelierui-private --host-port 18080 --container-port 8080
```

The lifecycle command requires the operator env file to be a regular mode-0600 file, an already-present image tag matching the supplied config ID, a pre-existing named network, and UID/GID 1000. Credentials stay in the env file and are not printed. Public signup is disabled by the lifecycle flags, so use an existing migrated account or an explicitly configured trusted-header setup; an empty-user copy has no first-account bootstrap in this helper. Conversion remains a separate target copy and does not activate or switch an installation. If preview reports blockers, conversion refuses by default; this example deliberately omits `--allow-partial-copy`.

`test_openatelierui_instance.py` uses mocked Docker commands and loopback health, plus the actual stdlib session-copy component. It checks copy and static merge, exact source/pre-session preservation, UID/env refusal, private file modes, flags/resource arguments, lifecycle ownership, restart failure cleanup, proxy bypass, secret-free CLI output and the legacy alias. It does not invoke Docker or make network requests. Separate primary-owned actual integration passed real-engine copy/start/status/stop/restart, bind-mount UID handling, published loopback health, imported sign-in/history and original preservation on a normal guest-only bridge in a no-NIC VM. Production named-network egress, general HTTP/user journeys, complete recovery and human UX/product acceptance remain separate qualifications.

## Bind mount grammar correction

The first isolated native lifecycle preparation was refused by Docker before a target database or application container was created: the helper emitted bare `rw` tokens in `--mount` specifications, which Docker requires to be key/value fields. The source database mount remains explicitly `ro`; the isolated prep target and the two runtime bind mounts now rely on Docker's writable default without adding a mount option. The mocked command test verifies that no bind mount contains a bare `rw` token and that source-only read-only and target/runtime destinations remain correct. The corrected command construction subsequently passed the actual isolated delivered lifecycle campaign described above. The optional non-container foreground launcher is a separate process qualification; direct host installation remains unqualified.

## Selected identity settings

The explicit `--include-identity-configuration` selector covers only validated boolean signup/login/admin-visibility values and the supported default-user role. Actual imported false/true/pending/true values were checked against deliberately opposite environment defaults, including refused signup and successful imported-account sign-in. Unsupported settings remain reported or archived; this is not migration of OAuth, MFA, arbitrary identity providers or all configuration. Review preview blockers before conversion.
