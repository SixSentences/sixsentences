#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
REPOSITORY_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd -P)"
COMPOSE_FILE="$REPOSITORY_ROOT/compose.yaml"
ENV_FILE="${SIX_SELFHOST_ENV_FILE:-$REPOSITORY_ROOT/.env.selfhost}"

"$SCRIPT_DIR/preflight.sh" "$ENV_FILE"

env_value() {
  local key="$1"
  awk -v key="$key" 'index($0, key "=") == 1 { print substr($0, length(key) + 2); exit }' \
    "$ENV_FILE" | tr -d '\r'
}

BACKUP_ROOT="$(env_value SIX_BACKUP_DIR)"
[[ "$BACKUP_ROOT" == /* ]] || {
  echo "Backup directory must be an absolute path." >&2
  exit 1
}
if [[ -L "$BACKUP_ROOT" ]]; then
  echo "Backup directory must not be a symlink." >&2
  exit 1
fi
mkdir -p -- "$BACKUP_ROOT"
chmod 700 "$BACKUP_ROOT"

COMPOSE=(docker compose --env-file "$ENV_FILE" --file "$COMPOSE_FILE")
"${COMPOSE[@]}" exec -T postgres pg_isready --username sixsentences --dbname sixsentences \
  >/dev/null

LOCK_DIR="$BACKUP_ROOT/.sixsentences-state-operation.lock"
if ! mkdir -m 700 "$LOCK_DIR" 2>/dev/null; then
  echo "Another backup or restore owns $LOCK_DIR; refusing to overlap state operations." >&2
  exit 1
fi

was_running() {
  local service="$1"
  local container_id
  container_id="$("${COMPOSE[@]}" ps --quiet "$service")"
  [[ -n "$container_id" ]] \
    && [[ "$(docker inspect --format '{{.State.Running}}' "$container_id")" == "true" ]]
}

API_WAS_RUNNING=0
WORKER_WAS_RUNNING=0
TEMP_DIR=""
was_running api && API_WAS_RUNNING=1
was_running worker && WORKER_WAS_RUNNING=1

image_identity() {
  local service="$1"
  local container_id
  if ! container_id="$("${COMPOSE[@]}" ps --all --quiet "$service")" \
    || [[ -z "$container_id" ]]; then
    printf 'unavailable'
    return
  fi
  docker inspect --format '{{.Config.Image}}|{{.Image}}' "$container_id" 2>/dev/null \
    || printf 'unavailable'
}

API_IMAGE_IDENTITY="$(image_identity api)"
WORKER_IMAGE_IDENTITY="$(image_identity worker)"
WEB_IMAGE_IDENTITY="$(image_identity web)"
POSTGRES_IMAGE_IDENTITY="$(image_identity postgres)"
PROXY_IMAGE_IDENTITY="$(image_identity proxy)"

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FINAL_DIR="$BACKUP_ROOT/$TIMESTAMP"
[[ ! -e "$FINAL_DIR" && ! -L "$FINAL_DIR" ]] || {
  echo "Backup target already exists: $FINAL_DIR" >&2
  exit 1
}
TEMP_DIR="$(mktemp -d "$BACKUP_ROOT/.pending-$TIMESTAMP.XXXXXX")"

resume_services() {
  local status=$?
  trap - EXIT HUP INT TERM
  if [[ "$API_WAS_RUNNING" == "1" ]]; then
    "${COMPOSE[@]}" up --detach --wait --wait-timeout 180 api >/dev/null || true
  fi
  if [[ "$WORKER_WAS_RUNNING" == "1" ]]; then
    "${COMPOSE[@]}" up --detach --wait --wait-timeout 180 worker >/dev/null || true
  fi
  if [[ $status -ne 0 ]]; then
    if [[ -n "$TEMP_DIR" ]]; then
      rm -rf -- "$TEMP_DIR"
    fi
    echo "Backup failed; services that were running were restarted." >&2
  fi
  rmdir -- "$LOCK_DIR" 2>/dev/null || true
  exit "$status"
}
trap resume_services EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

if [[ "$WORKER_WAS_RUNNING" == "1" ]]; then
  "${COMPOSE[@]}" stop --timeout 310 worker >/dev/null
fi
if [[ "$API_WAS_RUNNING" == "1" ]]; then
  "${COMPOSE[@]}" stop --timeout 30 api >/dev/null
fi

"${COMPOSE[@]}" exec -T postgres sh -eu -c \
  'exec pg_dump --format=custom --no-owner --no-privileges --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"' \
  >"$TEMP_DIR/database.dump"

"${COMPOSE[@]}" run --rm --no-deps -T --entrypoint sh api -eu -c '
  if find /data ! -type f ! -type d -print -quit | grep -q .; then
    echo "Refusing to archive a data volume containing a special file or link." >&2
    exit 1
  fi
  exec tar --hard-dereference -czf - -C /data .
' >"$TEMP_DIR/app-data.tar.gz"

"${COMPOSE[@]}" run --rm --no-deps -T --entrypoint sh api -eu -c '
  if find /privacy ! -type f ! -type d -print -quit | grep -q .; then
    echo "Refusing to archive a privacy volume containing a special file or link." >&2
    exit 1
  fi
  if find /privacy -mindepth 1 ! -path /privacy/erasure-ledger.jsonl -print -quit \
    | grep -q .; then
    echo "Refusing to archive an unexpected privacy-volume entry." >&2
    exit 1
  fi
  exec tar --hard-dereference -czf - -C /privacy .
' >"$TEMP_DIR/privacy-data.tar.gz"

tar -tzf "$TEMP_DIR/app-data.tar.gz" >/dev/null
tar -tzf "$TEMP_DIR/privacy-data.tar.gz" >/dev/null

{
  printf 'format=1\n'
  printf 'created_utc=%s\n' "$TIMESTAMP"
  printf 'database=postgresql\n'
  printf 'database_name=sixsentences\n'
  printf 'includes=database,app-data,privacy-data\n'
  printf 'api_image=%s\n' "$API_IMAGE_IDENTITY"
  printf 'worker_image=%s\n' "$WORKER_IMAGE_IDENTITY"
  printf 'web_image=%s\n' "$WEB_IMAGE_IDENTITY"
  printf 'postgres_image=%s\n' "$POSTGRES_IMAGE_IDENTITY"
  printf 'proxy_image=%s\n' "$PROXY_IMAGE_IDENTITY"
  if command -v git >/dev/null 2>&1; then
    printf 'source_revision=%s\n' "$(git -C "$REPOSITORY_ROOT" rev-parse --verify HEAD 2>/dev/null || printf unknown)"
  else
    printf 'source_revision=unknown\n'
  fi
} >"$TEMP_DIR/METADATA"

if command -v sha256sum >/dev/null 2>&1; then
  (
    cd "$TEMP_DIR"
    sha256sum database.dump app-data.tar.gz privacy-data.tar.gz METADATA >SHA256SUMS
  )
else
  (
    cd "$TEMP_DIR"
    shasum -a 256 database.dump app-data.tar.gz privacy-data.tar.gz METADATA >SHA256SUMS
  )
fi
chmod -R go-rwx "$TEMP_DIR"
mv -- "$TEMP_DIR" "$FINAL_DIR"

if [[ "$API_WAS_RUNNING" == "1" ]]; then
  "${COMPOSE[@]}" up --detach --wait --wait-timeout 180 api >/dev/null
fi
if [[ "$WORKER_WAS_RUNNING" == "1" ]]; then
  "${COMPOSE[@]}" up --detach --wait --wait-timeout 180 worker >/dev/null
fi
rmdir -- "$LOCK_DIR"
trap - EXIT HUP INT TERM

echo "Backup completed: $FINAL_DIR"
echo "Keep a separately encrypted copy and test restore with deploy/community/restore.sh."
