#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
SERVICE_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd -P)"
COMPOSE_FILE="$SERVICE_ROOT/compose.yaml"
ENV_FILE="${SIX_COMMUNITY_ENV_FILE:-$SERVICE_ROOT/.env}"
BACKUP_DIR=""
CONFIRMATION=""

usage() {
  echo "Usage: restore.sh --backup ABSOLUTE_BACKUP_DIRECTORY --confirm RESTORE" >&2
}

while (($#)); do
  case "$1" in
    --backup) [[ $# -ge 2 ]] || { usage; exit 2; }; BACKUP_DIR="$2"; shift 2 ;;
    --confirm) [[ $# -ge 2 ]] || { usage; exit 2; }; CONFIRMATION="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) usage; exit 2 ;;
  esac
done

[[ "$CONFIRMATION" == RESTORE ]] || {
  echo "Refusing restore without --confirm RESTORE." >&2
  exit 2
}
[[ "$BACKUP_DIR" == /* && -d "$BACKUP_DIR" && ! -L "$BACKUP_DIR" ]] || {
  echo "The backup must be an absolute, non-symlink directory." >&2
  exit 2
}
[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || {
  echo "The community environment file is missing or unsafe." >&2
  exit 1
}
BACKUP_DIR="$(CDPATH= cd -- "$BACKUP_DIR" && pwd -P)"

for required in database.dump app-data.tar.gz privacy-data.tar.gz METADATA SHA256SUMS; do
  [[ -f "$BACKUP_DIR/$required" && ! -L "$BACKUP_DIR/$required" ]] || {
    echo "The backup state set is incomplete." >&2
    exit 1
  }
done
LC_ALL=C awk '
  NF != 2 || length($1) != 64 || $1 !~ /^[0-9a-f]+$/ { exit 1 }
  $2 == "database.dump" { database += 1; next }
  $2 == "app-data.tar.gz" { app += 1; next }
  $2 == "privacy-data.tar.gz" { privacy += 1; next }
  $2 == "METADATA" { metadata += 1; next }
  { exit 1 }
  END { exit database == 1 && app == 1 && privacy == 1 && metadata == 1 ? 0 : 1 }
' "$BACKUP_DIR/SHA256SUMS" || {
  echo "The checksum manifest is not an exact community state inventory." >&2
  exit 1
}
if command -v sha256sum >/dev/null 2>&1; then
  (cd "$BACKUP_DIR" && sha256sum --check SHA256SUMS)
else
  (cd "$BACKUP_DIR" && shasum -a 256 --check SHA256SUMS)
fi
grep -qx 'format=1' "$BACKUP_DIR/METADATA"
grep -qx 'database=postgresql' "$BACKUP_DIR/METADATA"
grep -qx 'includes=database,app-data,privacy-data' "$BACKUP_DIR/METADATA"

validate_archive() {
  local archive="$1"
  tar -tzf "$archive" | awk '
    /^\// || /^\.\.\// || /\/\.\.\// || /\/\.\.$/ { exit 1 }
  '
  tar -tvzf "$archive" | awk '$1 !~ /^[-d]/ { exit 1 }'
}
validate_archive "$BACKUP_DIR/app-data.tar.gz"
validate_archive "$BACKUP_DIR/privacy-data.tar.gz"
tar -tzf "$BACKUP_DIR/privacy-data.tar.gz" | awk '
  $0 == "." || $0 == "./" || $0 == "erasure-ledger.jsonl" || $0 == "./erasure-ledger.jsonl" { next }
  { exit 1 }
'

COMPOSE=(docker compose --env-file "$ENV_FILE" --file "$COMPOSE_FILE")
LOCK_PARENT="$(dirname "$BACKUP_DIR")"
LOCK_DIR="$LOCK_PARENT/.sixsentences-state-operation.lock"
mkdir -m 700 "$LOCK_DIR" 2>/dev/null || {
  echo "Another backup or restore is active." >&2
  exit 1
}
STAGE="$(mktemp -d "$LOCK_PARENT/.restore-stage.XXXXXX")"

cleanup_tree() {
  local path="$1"
  if [[ -d "$path" && ! -L "$path" ]]; then
    find "$path" -depth -mindepth 1 -delete
    rmdir "$path"
  fi
}
cleanup() {
  local status=$?
  trap - EXIT HUP INT TERM
  cleanup_tree "$STAGE" 2>/dev/null || true
  rmdir "$LOCK_DIR" 2>/dev/null || true
  [[ $status -eq 0 ]] || echo "Restore failed; API and worker remain stopped." >&2
  exit "$status"
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

mkdir -m 700 "$STAGE/candidate"
tar -xzf "$BACKUP_DIR/privacy-data.tar.gz" --no-same-owner --no-same-permissions -C "$STAGE/candidate"
"${COMPOSE[@]}" up --detach --wait postgres >/dev/null
"${COMPOSE[@]}" run --rm --no-deps -T --user 0:0 \
  --volume "$STAGE/candidate:/candidate:ro" \
  --env SIX_ERASURE_LEDGER_PATH=/candidate/erasure-ledger.jsonl \
  --entrypoint six-community-erasure api verify >/dev/null

"${COMPOSE[@]}" stop --timeout 310 worker >/dev/null || true
"${COMPOSE[@]}" stop --timeout 30 api >/dev/null || true
"${COMPOSE[@]}" run --rm --no-deps -T --user 0:0 \
  --entrypoint six-community-erasure api verify >/dev/null
LEDGER_SOURCE="$(
  "${COMPOSE[@]}" run --rm --no-deps -T --user 0:0 \
    --volume "$STAGE/candidate:/candidate:ro" \
    --entrypoint six-community-recovery api \
    /privacy/erasure-ledger.jsonl /candidate/erasure-ledger.jsonl
)"
case "$LEDGER_SOURCE" in current|snapshot) ;; *) exit 1 ;; esac

if [[ "$LEDGER_SOURCE" == snapshot ]]; then
  "${COMPOSE[@]}" run --rm --no-deps -T --user 0:0 --entrypoint sh api -eu -c '
    find /privacy -depth -mindepth 1 -delete
    tar -xzf - --no-same-owner --no-same-permissions -C /privacy
  ' <"$BACKUP_DIR/privacy-data.tar.gz"
fi
"${COMPOSE[@]}" exec -T postgres sh -eu -c '
  dropdb --if-exists --force --username "$POSTGRES_USER" "$POSTGRES_DB"
  createdb --username "$POSTGRES_USER" --owner "$POSTGRES_USER" "$POSTGRES_DB"
'
"${COMPOSE[@]}" exec -T postgres sh -eu -c \
  'exec pg_restore --exit-on-error --no-owner --no-privileges --username "$POSTGRES_USER" --dbname "$POSTGRES_DB"' \
  <"$BACKUP_DIR/database.dump"
"${COMPOSE[@]}" run --rm --no-deps -T --user 0:0 --entrypoint sh api -eu -c '
  find /data -depth -mindepth 1 -delete
  tar -xzf - --no-same-owner --no-same-permissions -C /data
' <"$BACKUP_DIR/app-data.tar.gz"

"${COMPOSE[@]}" run --rm --no-deps -T migrate
"${COMPOSE[@]}" run --rm --no-deps -T --entrypoint six-community-erasure api replay
"${COMPOSE[@]}" run --rm --no-deps -T --entrypoint six-community api corpus verify
"${COMPOSE[@]}" up --detach --wait api worker

cleanup_tree "$STAGE"
STAGE=""
rmdir "$LOCK_DIR"
trap - EXIT HUP INT TERM
echo "Restore completed; database, files and erasure replay passed validation."
