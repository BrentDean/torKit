#!/bin/sh
set -eu

umask 077

runtime_dir="${TORKIT_RUNTIME_DIR:-/var/lib/torkit}"
config_dir="${XDG_CONFIG_HOME:-${runtime_dir}/config}"

mkdir -p \
    "${runtime_dir}" \
    "${config_dir}"

chmod 0700 "${runtime_dir}" "${config_dir}"

export TORKIT_RUNTIME_DIR="${runtime_dir}"
export XDG_CONFIG_HOME="${config_dir}"
export TMPDIR=/tmp

if ! command -v tor >/dev/null 2>&1; then
    printf '%s\n' "torKit container error: tor executable not found" >&2
    exit 127
fi

if ! command -v torkit-cli >/dev/null 2>&1; then
    printf '%s\n' "torKit container error: torkit-cli executable not found" >&2
    exit 127
fi

exec python3 /usr/local/lib/torkit/run-service.py "$@"
