#!/bin/bash
set -eu

if [ "$#" -ne 0 ]; then
    printf '%s\n' 'This compatibility entrypoint accepts no command arguments.' >&2
    exit 2
fi

if [ -z "${WEBUI_SECRET_KEY:-}" ]; then
    printf '%s\n' 'WEBUI_SECRET_KEY must be supplied by the operator.' >&2
    exit 2
fi

bootstrap_mode=${OPENATELIER_ATLAS_BOOTSTRAP:-0}
case "$bootstrap_mode" in
    0) ;;
    1)
        if [ -z "${OPENATELIER_BOOTSTRAP_SCRIPT:-}" ] || [ -z "${OPENATELIER_BOOTSTRAP_SHA256:-}" ]; then
            printf '%s\n' 'Explicit bootstrap script and SHA-256 are required.' >&2
            exit 2
        fi
        export PYTHONPATH="/app/backend:/opt/atlas:${PYTHONPATH:-}"
        if ! python -m open_webui.utils.async_model_bootstrap \
            "$OPENATELIER_BOOTSTRAP_SCRIPT" "$OPENATELIER_BOOTSTRAP_SHA256"; then
            printf '%s\n' 'Selected bootstrap failed; the application was not started.' >&2
            exit 1
        fi
        ;;
    *)
        printf '%s\n' 'OPENATELIER_ATLAS_BOOTSTRAP must be 0 or 1.' >&2
        exit 2
        ;;
esac

exec bash /app/backend/start.sh
