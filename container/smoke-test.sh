#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_root"

image="${TORKIT_SMOKE_IMAGE:-torkit:smoke}"
smoke_project="torkit-smoke-$$"
temp_dir="$(mktemp -d)"
trap 'rm -rf -- "$temp_dir"' EXIT
mkdir -p "$temp_dir"/{share,receive,website,chat-state,board-state,share-state,receive-state,website-state,wrapper-state,launcher-state,migration-state}
chmod 700 "$temp_dir"/*-state

build_id="smoke-$(date +%s)"
printf 'Building %s\n' "$image"
docker build \
  --pull \
  --build-arg "TORKIT_BUILD_ID=$build_id" \
  --tag "$image" \
  .

actual_build_id="$(docker image inspect --format '{{index .Config.Labels "org.torkit.build-id"}}' "$image")"
[[ "$actual_build_id" == "$build_id" ]]

help_output="$(docker run --rm --network none "$image" --help)"
for option in --chat --board --receive --website --no-autostop-sharing --persistent --local-only; do
  printf '%s\n' "$help_output" | grep -F -- "$option" >/dev/null
done

docker run --rm --network none --entrypoint /bin/sh "$image" -c '
  set -eu
  command -v tor >/dev/null
  command -v torkit-cli >/dev/null
  test -x /usr/local/lib/torkit/run-service.py
  python -c "import flask, flask_socketio, nacl, stem"
'

printf '%s\n' "Testing Board persistence and CSRF in the built runtime image"
docker run --rm --network none --entrypoint python3 \
  --mount "type=bind,src=$repo_root/container/board-smoke.py,target=/tmp/board-smoke.py,readonly" \
  "$image" /tmp/board-smoke.py

cat >"$temp_dir/fake-cli" <<'FAKE'
#!/bin/sh
printf '%s\n' 'torKit fake service'
printf '%s\n' 'Give this address and private key to the recipient:'
printf '%s\n' 'http://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.onion'
printf '%s\n' 'Private key: ABCDEFGHIJKLMNOPQRSTUVWXYZ234567ABCDEFGHIJKLMNOPQRSTUV'
printf '%s\n' 'Press Ctrl+C to stop the server'
FAKE
chmod 755 "$temp_dir/fake-cli"

cat >"$temp_dir/wrapper-state/session.json" <<'JSON'
{
  "persistent": {"enabled": true, "mode": "chat"},
  "general": {"title": "Old title", "public": false},
  "onion": {
    "private_key": "keep-onion-key",
    "client_auth_priv_key": "keep-client-private",
    "client_auth_pub_key": "keep-client-public"
  },
  "chat": {}
}
JSON
chmod 600 "$temp_dir/wrapper-state/session.json"

wrapper_output="$(
  docker run --rm --network none \
    --user "$(id -u):$(id -g)" \
    --env TORKIT_CLI=/usr/local/bin/fake-cli \
    --env TORKIT_MODE=chat \
    --env 'TORKIT_TITLE=Onion secured' \
    --mount "type=bind,src=$temp_dir/fake-cli,target=/usr/local/bin/fake-cli,readonly" \
    --mount "type=bind,src=$temp_dir/wrapper-state,target=/var/lib/torkit" \
    "$image" \
    --chat --persistent /var/lib/torkit/session.json
)"

printf '%s\n' "$wrapper_output" | grep -F -- 'Onion service published. Access information stored securely.' >/dev/null
! printf '%s\n' "$wrapper_output" | grep -F -- 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567' >/dev/null
! printf '%s\n' "$wrapper_output" | grep -F -- 'http://aaaaaaaa' >/dev/null

grep -F -- 'ONION_ADDRESS=http://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.onion' \
  "$temp_dir/wrapper-state/access.env" >/dev/null
grep -F -- 'ACCESS_KEY=ABCDEFGHIJKLMNOPQRSTUVWXYZ234567ABCDEFGHIJKLMNOPQRSTUV' \
  "$temp_dir/wrapper-state/access.env" >/dev/null
[[ "$(stat -c '%a' "$temp_dir/wrapper-state/access.env")" == 600 ]]
[[ ! -e "$temp_dir/wrapper-state/service.pid" ]]

python3 - "$temp_dir/wrapper-state/session.json" <<'PY'
import json
import sys
from pathlib import Path

session = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert session["general"]["title"] == "Onion secured"
assert session["onion"]["private_key"] == "keep-onion-key"
assert session["onion"]["client_auth_priv_key"] == "keep-client-private"
PY

TORKIT_IMAGE="$image" \
TORKIT_BUILD_ID="$build_id" \
TORKIT_CONTAINER_UID="$(id -u)" \
TORKIT_CONTAINER_GID="$(id -g)" \
TORKIT_FILESHARE_PATH="$temp_dir/share" \
TORKIT_RECEIVE_PATH="$temp_dir/receive" \
TORKIT_WEBSITE_PATH="$temp_dir/website" \
TORKIT_CHAT_STATE="$temp_dir/chat-state" \
TORKIT_BOARD_STATE="$temp_dir/board-state" \
TORKIT_FILESHARE_STATE="$temp_dir/share-state" \
TORKIT_RECEIVE_STATE="$temp_dir/receive-state" \
TORKIT_WEBSITE_STATE="$temp_dir/website-state" \
docker compose \
  --project-name "$smoke_project" \
  --profile chat \
  --profile board \
  --profile fileshare \
  --profile receive \
  --profile website \
  config >/dev/null

bash -n ./torkit
python3 -m py_compile container/run-service.py container/torkit_host/*.py

launcher_help="$(./torkit --help)"
for text in \
  './torkit              Select services to start' \
  './torkit up           Select services to start' \
  './torkit chat         Start or resume disposable live Chat' \
  './torkit board        Start or resume persistent message Board' \
  './torkit messageboard Alias for ./torkit board' \
  './torkit down         Select running services to stop; preserve identity' \
  './torkit restart      Select running services to restart' \
  './torkit reset        Select services to give new addresses and keys' \
  './torkit burn SERVICE Burn selected service identities and runtime state' \
  './torkit operator     Start the localhost-only operator console' \
  './torkit nuke         Destroy all TorKit containers, image, and identities' \
  './torkit ps           Show Docker container diagnostics' \
  './torkit status       Show running onion services and access keys' \
  "$HOME/.torkit/config.env" \
  'Docker is the default runtime' \
  'Onion client authorization is enabled'
do
  printf '%s\n' "$launcher_help" | grep -F -- "$text" >/dev/null
done

! printf '%s\n' "$launcher_help" | grep -F -- './torkit chat up' >/dev/null
! printf '%s\n' "$launcher_help" | grep -F -- 'SERVICE info' >/dev/null
! printf '%s\n' "$launcher_help" | grep -F -- 'SERVICE link' >/dev/null

TORKIT_IMAGE="$image" TORKIT_PROJECT_NAME="$smoke_project" TORKIT_STATE_DIR="$temp_dir/launcher-state" ./torkit ps >/dev/null
config_file="$temp_dir/launcher-state/config.env"
[[ -f "$config_file" ]]
[[ "$(stat -c '%a' "$config_file")" == 600 ]]
grep -F -- 'CHAT_NAME=Onion secured' "$config_file" >/dev/null
grep -F -- 'BOARD_NAME=Private board' "$config_file" >/dev/null
grep -F -- "SHARE_DIR=$HOME/onion_share" "$config_file" >/dev/null
grep -F -- "RECEIVE_DIR=$HOME/onion_receive" "$config_file" >/dev/null
grep -F -- "WEBSITE_DIR=$HOME/onion_website" "$config_file" >/dev/null

up_quit_output="$(
  printf 'q\n' |
    TORKIT_IMAGE="$image" TORKIT_PROJECT_NAME="$smoke_project" TORKIT_STATE_DIR="$temp_dir/launcher-state" ./torkit up
)"
printf '%s\n' "$up_quit_output" | grep -F -- 'No services started.' >/dev/null

down_empty_output="$(
  TORKIT_IMAGE="$image" TORKIT_PROJECT_NAME="$smoke_project" TORKIT_STATE_DIR="$temp_dir/launcher-state" ./torkit down
)"
printf '%s\n' "$down_empty_output" | grep -F -- 'No TorKit services are running.' >/dev/null

restart_empty_output="$(
  TORKIT_IMAGE="$image" TORKIT_PROJECT_NAME="$smoke_project" TORKIT_STATE_DIR="$temp_dir/launcher-state" ./torkit restart
)"
printf '%s\n' "$restart_empty_output" | grep -F -- 'No TorKit services are running.' >/dev/null

reset_quit_output="$(
  printf 'q\n' |
    TORKIT_IMAGE="$image" TORKIT_PROJECT_NAME="$smoke_project" TORKIT_STATE_DIR="$temp_dir/launcher-state" ./torkit reset
)"
printf '%s\n' "$reset_quit_output" | grep -F -- 'a. All' >/dev/null
printf '%s\n' "$reset_quit_output" | grep -F -- 'No services reset.' >/dev/null

burn_cancel_output="$(
  printf 'no\n' |
    TORKIT_IMAGE="$image" TORKIT_PROJECT_NAME="$smoke_project" TORKIT_STATE_DIR="$temp_dir/launcher-state" ./torkit burn chat
)"
printf '%s\n' "$burn_cancel_output" | grep -F -- 'Burn cancelled.' >/dev/null

nuke_cancel_output="$(
  printf 'no\n' |
    TORKIT_IMAGE="$image" TORKIT_PROJECT_NAME="$smoke_project" TORKIT_STATE_DIR="$temp_dir/launcher-state" ./torkit nuke
)"
printf '%s\n' "$nuke_cancel_output" | grep -F -- 'Nuke cancelled.' >/dev/null

health_empty_output="$(
  TORKIT_IMAGE="$image" TORKIT_PROJECT_NAME="$smoke_project" TORKIT_STATE_DIR="$temp_dir/launcher-state" ./torkit health --json
)"
printf '%s' "$health_empty_output" | python3 -c '
import json, sys
data = json.load(sys.stdin)
assert set(data) == {"chat", "board", "share", "receive", "website"}
assert all(item["state"] == "absent" for item in data.values())
assert all(not item["ready"] for item in data.values())
assert "ACCESS_KEY" not in str(data)
'

status_empty_output="$(
  TORKIT_IMAGE="$image" TORKIT_PROJECT_NAME="$smoke_project" TORKIT_STATE_DIR="$temp_dir/launcher-state" ./torkit status
)"
printf '%s\n' "$status_empty_output" | grep -F -- 'No TorKit services are running.' >/dev/null

printf '%s\n' "$temp_dir/share" >"$temp_dir/migration-state/share-path"
TORKIT_IMAGE="$image" TORKIT_PROJECT_NAME="$smoke_project" TORKIT_STATE_DIR="$temp_dir/migration-state" ./torkit ps >/dev/null
grep -F -- "SHARE_DIR=$temp_dir/share" "$temp_dir/migration-state/config.env" >/dev/null
[[ ! -e "$temp_dir/migration-state/share-path" ]]

mkdir -p "$temp_dir/legacy-state" "$temp_dir/migrated-home"
cat >"$temp_dir/legacy-state/config.env" <<EOF
CHAT_NAME=Legacy chat
SHARE_DIR=$temp_dir/share
RECEIVE_DIR=$temp_dir/receive
WEBSITE_DIR=$temp_dir/website
EOF
HOME="$temp_dir/migrated-home" \
TORKIT_LEGACY_STATE_DIR="$temp_dir/legacy-state" \
TORKIT_IMAGE="$image" \
TORKIT_PROJECT_NAME="$smoke_project" \
./torkit ps >/dev/null
migrated_config="$temp_dir/migrated-home/.torkit/config.env"
[[ -f "$migrated_config" ]]
[[ "$(stat -c '%a' "$temp_dir/migrated-home/.torkit")" == 700 ]]
[[ "$(stat -c '%a' "$migrated_config")" == 600 ]]
grep -F -- 'CHAT_NAME=Legacy chat' "$migrated_config" >/dev/null
grep -F -- 'BOARD_NAME=Private board' "$migrated_config" >/dev/null
grep -F -- "SHARE_DIR=$temp_dir/share" "$migrated_config" >/dev/null

set +e
old_output="$(TORKIT_PROJECT_NAME="$smoke_project" TORKIT_STATE_DIR="$temp_dir/launcher-state" ./torkit chat up 2>&1)"
old_status=$?
set -e
[[ "$old_status" -ne 0 ]]
printf '%s\n' "$old_output" | grep -F -- './torkit up' >/dev/null

PYTHONPATH=container python3 -m unittest discover -s container/tests -v

printf '%s\n' 'torKit lifecycle command smoke test: PASS'
