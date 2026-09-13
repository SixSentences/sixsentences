#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
SERVICE_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd -P)"
COMPOSE_FILE="$SERVICE_ROOT/compose.yaml"
ENV_FILE="${SIX_COMMUNITY_ENV_FILE:-$SERVICE_ROOT/.env}"
DESTINATION=""

usage() {
  echo "Usage: backup.sh --destination ABSOLUTE_DIRECTORY" >&2
}

while (($#)); do
  case "$1" in
    --destination) [[ $# -ge 2 ]] || { usage; exit 2; }; DESTINATION="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

[[ "$DESTINATION" == /* && ! -L "$DESTINATION" ]] || {
  echo "The destination must be an absolute, non-symlink path." >&2
  exit 2
}
[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || {
  echo "The community environment file is missing or unsafe." >&2
  exit 1
}
mkdir -p -- "$DESTINATION"
DESTINATION="$(CDPATH= cd -- "$DESTINATION" && pwd -P)"
chmod 700 "$DESTINATION"

COMPOSE=(docker compose --env-file "$ENV_FILE" --file "$COMPOSE_FILE")
LOCK_DIR="$DESTINATION/.sixsentences-state-operation.lock"
mkdir -m 700 "$LOCK_DIR" 2>/dev/null || {
  echo "Another backup or restore is active." >&2
  exit 1
}

TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
FINAL_DIR="$DESTINATION/$TIMESTAMP"
[[ ! -e "$FINAL_DIR" && ! -L "$FINAL_DIR" ]] || {
  rmdir "$LOCK_DIR"
  echo "The timestamped backup target already exists." >&2
  exit 1
}
PENDING_DIR="$(mktemp -d "$DESTINATION/.pending-$TIMESTAMP.XXXXXX")"
API_WAS_RUNNING=0
WORKER_WAS_RUNNING=0
[[ -n "$("${COMPOSE[@]}" ps --status running --quiet api)" ]] && API_WAS_RUNNING=1
[[ -n "$("${COMPOSE[@]}" ps --status running --quiet worker)" ]] && WORKER_WAS_RUNNING=1

cleanup_tree() {
  local path="$1"
  if [[ -d "$path" && ! -L "$path" ]]; then
    find "$path" -depth -mindepth 1 -delete
    rmdir "$path"
  fi
}

finish() {
  local status=$?
  trap - EXIT HUP INT TERM
  if [[ "$API_WAS_RUNNING" == 1 ]]; then
    "${COMPOSE[@]}" up --detach --wait api >/dev/null || true
  fi
  if [[ "$WORKER_WAS_RUNNING" == 1 ]]; then
    "${COMPOSE[@]}" up --detach --wait worker >/dev/null || true
  fi
  if [[ $status -ne 0 ]]; then
    cleanup_tree "$PENDING_DIR" 2>/dev/null || true
    echo "Backup failed; persistent state was not published." >&2
  fi
  rmdir "$LOCK_DIR" 2>/dev/null || true
  exit "$status"
}
trap finish EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

"${COMPOSE[@]}" up --detach --wait postgres >/dev/null
[[ "$WORKER_WAS_RUNNING" == 1 ]] && "${COMPOSE[@]}" stop --timeout 310 worker >/dev/null
[[ "$API_WAS_RUNNING" == 1 ]] && "${COMPOSE[@]}" stop --timeout 30 api >/dev/null

"${COMPOSE[@]}" exec -T postgres sh -eu -c \
  'exec pg_dump --format=custom --no-owner --no-privileges --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"' \
  >"$PENDING_DIR/database.dump"
"${COMPOSE[@]}" run --rm --no-deps -T --entrypoint sh api -eu -c '
  if find /data ! -type f ! -type d -print -quit | grep -q .; then exit 1; fi
  exec tar --hard-dereference -czf - -C /data .
' >"$PENDING_DIR/app-data.tar.gz"
"${COMPOSE[@]}" run --rm --no-deps -T --entrypoint sh api -eu -c '
  if find /privacy ! -type f ! -type d -print -quit | grep -q .; then exit 1; fi
  if find /privacy -mindepth 1 ! -path /privacy/erasure-ledger.jsonl -print -quit | grep -q .; then exit 1; fi
  exec tar --hard-dereference -czf - -C /privacy .
' >"$PENDING_DIR/privacy-data.tar.gz"
tar -tzf "$PENDING_DIR/app-data.tar.gz" >/dev/null
tar -tzf "$PENDING_DIR/privacy-data.tar.gz" >/dev/null

{
  echo "format=1"
  echo "created_utc=$TIMESTAMP"
  echo "database=postgresql"
  echo "includes=database,app-data,privacy-data"
} >"$PENDING_DIR/METADATA"
if command -v sha256sum >/dev/null 2>&1; then
  (cd "$PENDING_DIR" && sha256sum database.dump app-data.tar.gz privacy-data.tar.gz METADATA >SHA256SUMS)
else
  (cd "$PENDING_DIR" && shasum -a 256 database.dump app-data.tar.gz privacy-data.tar.gz METADATA >SHA256SUMS)
fi
find "$PENDING_DIR" -type d -exec chmod 700 {} +
find "$PENDING_DIR" -type f -exec chmod 600 {} +
mv -- "$PENDING_DIR" "$FINAL_DIR"
PENDING_DIR=""

[[ "$API_WAS_RUNNING" == 1 ]] && "${COMPOSE[@]}" up --detach --wait api >/dev/null
[[ "$WORKER_WAS_RUNNING" == 1 ]] && "${COMPOSE[@]}" up --detach --wait worker >/dev/null
rmdir "$LOCK_DIR"
trap - EXIT HUP INT TERM
echo "Backup completed: $FINAL_DIR"
