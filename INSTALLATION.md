# Installation, backup and rollback

OpenAtelierUI is still a development candidate. Use a closed copy of data, keep the original installation available and do not expose the inherited baseline directly to the public Internet.

The currently qualified operator path uses a locally loaded, pinned image and the standard-library lifecycle helper. Docker is an isolation and reproducibility option, not a product requirement or the only intended future installation method. Direct host installation remains under qualification and is not documented as production-ready here.

## Prerequisites

- Linux operator account with UID and GID 1000.
- Python 3.8 or later.
- A local OpenAtelierUI image already loaded; no pull is performed by the helper.
- A pre-existing private runtime network selected by the operator. On Docker 28.1.1, an `--internal` bridge suppresses the loopback port publication used by this candidate; the measured path uses a bridge with outbound masquerading disabled instead.
- A closed source-data copy containing `webui.db` and `uploads/`.
- A mode-0600 environment file containing `WEBUI_SECRET_KEY`. Its contents are never printed by the helper.

Record the exact local image identity before starting:

```sh
IMAGE_TAG=openatelierui-candidate:local
IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$IMAGE_TAG")"
printf '%s\n' "$IMAGE_ID"
```

Create the private bridge and environment file without exposing their values:

```sh
docker network create --driver bridge \
  --opt com.docker.network.bridge.enable_ip_masquerade=false \
  openatelierui-private

umask 077
{ printf 'WEBUI_SECRET_KEY='; openssl rand -hex 32; } > /srv/openatelierui/operator.env
chmod 0600 /srv/openatelierui/operator.env
```

The no-masquerade bridge avoids ordinary outbound Internet routing in the measured Docker setup. It is not a substitute for host firewall policy or deployment-specific network review.

## Preview migration on a copy

Never point a migration command at the active data directory. Begin with the generic importer help and create a new preview workspace:

```sh
python3 validation/migrate_sqlite.py --help
python3 validation/import_private_notes.py --help
```

Preview must leave the target absent. Conversion requires an explicit new target and preserves the source bytes. Unsupported schemas, rights and configuration remain blockers or archive-only records; they are not silently discarded.

## Start, inspect and stop

```sh
python3 validation/openatelierui_instance.py start \
  --image "$IMAGE_TAG" \
  --expected-image-id "$IMAGE_ID" \
  --source-data /srv/openatelierui/copied-data \
  --env-file /srv/openatelierui/operator.env \
  --workspace /srv/openatelierui/instances/review-01 \
  --network openatelierui-private \
  --host-port 18080 \
  --container-port 8080

python3 validation/openatelierui_instance.py status \
  --workspace /srv/openatelierui/instances/review-01

python3 validation/openatelierui_instance.py stop \
  --workspace /srv/openatelierui/instances/review-01
```

The application binds to loopback. The helper pins the image identity, runs non-root with a read-only root filesystem, drops capabilities, applies resource limits and refuses workspace or mount identities it does not own.

## Backup checkpoint

Stop the candidate normally before copying its workspace. Retain all three independent recovery inputs:

1. the untouched original data directory;
2. the converted copy used to create the candidate workspace;
3. the stopped candidate workspace and its private manifest.

Store the image ID, archive hash, application version and migration receipt beside the backup. A hash is evidence of identity, not a substitute for the recovery bytes.

## Restart

```sh
python3 validation/openatelierui_instance.py restart \
  --workspace /srv/openatelierui/instances/review-01
```

After restart, verify sign-in, saved conversations, files, notes, model/tool visibility and user isolation before considering any cutover.

## Rollback

Rollback does not rewrite the original installation:

1. stop the candidate workspace;
2. preserve its receipt and failed state for diagnosis;
3. keep the converted copy unchanged;
4. start the previous installation against its original data and image using its existing procedure;
5. verify sign-in and saved work before reopening access.

Do not copy a candidate database back over the original. Reverse migration is not qualified. If normal stop cannot be confirmed, do not reuse or delete the workspace until its processes and mounts have been inspected.

## Current limits

- Real private-data cutover has not been authorized or qualified.
- Direct host installation and automatic updates are still under qualification.
- Unsupported configuration domains remain archive-only or refused.
- Optional image generation requires a separate runtime qualification.
- Human accessibility and product acceptance remain open.
