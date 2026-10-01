#!/usr/bin/env bash
set -Eeuo pipefail

RESTORE_PHASE=configuration
# Only a fixed phase label reaches CI diagnostics, never a command or payload.
trap 'printf "SIX_RESTORE_FAILURE_PHASE=%s\n" "$RESTORE_PHASE" >&2' ERR

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
REPOSITORY_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd -P)"
COMPOSE_FILE="$REPOSITORY_ROOT/compose.yaml"
ENV_FILE="${SIX_SELFHOST_ENV_FILE:-$REPOSITORY_ROOT/.env.selfhost}"
BACKUP_DIR=""
CONFIRMATION=""

usage() {
  cat <<'EOF'
Usage: restore.sh --backup ABSOLUTE_BACKUP_DIRECTORY --confirm RESTORE

This replaces the community database and application data. It selects the
newest authenticated erasure journal and replays it before reopening traffic.
Verification or shutdown failure prevents persistent-state replacement.
A failed restore requires inspection before reopening traffic.
EOF
}

while (($#)); do
  case "$1" in
    --backup)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      BACKUP_DIR="$2"
      shift 2
      ;;
    --confirm)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      CONFIRMATION="$2"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

[[ "$CONFIRMATION" == "RESTORE" ]] || {
  echo "Refusing destructive restore without --confirm RESTORE." >&2
  exit 2
}
[[ "$BACKUP_DIR" == /* && -d "$BACKUP_DIR" && ! -L "$BACKUP_DIR" ]] || {
  echo "--backup must name an existing, non-symlink absolute directory." >&2
  exit 2
}

"$SCRIPT_DIR/preflight.sh" "$ENV_FILE"

env_value() {
  local key="$1"
  awk -v key="$key" 'index($0, key "=") == 1 { print substr($0, length(key) + 2); exit }' \
    "$ENV_FILE" | tr -d '\r'
}

CONFIGURED_BACKUP_ROOT="$(env_value SIX_BACKUP_DIR)"
RESTORE_PHASE=backup_manifest
CANONICAL_ROOT="$(CDPATH= cd -- "$CONFIGURED_BACKUP_ROOT" && pwd -P)"
CANONICAL_BACKUP="$(CDPATH= cd -- "$BACKUP_DIR" && pwd -P)"
case "$CANONICAL_BACKUP/" in
  "$CANONICAL_ROOT"/*) ;;
  *) echo "Backup must be a direct descendant of SIX_BACKUP_DIR." >&2; exit 1 ;;
esac

for required in database.dump app-data.tar.gz privacy-data.tar.gz METADATA SHA256SUMS; do
  [[ -f "$CANONICAL_BACKUP/$required" && ! -L "$CANONICAL_BACKUP/$required" ]] || {
    echo "Backup is missing regular file: $required" >&2
    exit 1
  }
done

if ! LC_ALL=C awk '
  NF != 2 || length($1) != 64 || $1 !~ /^[0-9a-f]+$/ { exit 1 }
  $2 == "database.dump" { database += 1; next }
  $2 == "app-data.tar.gz" { app_data += 1; next }
  $2 == "privacy-data.tar.gz" { privacy_data += 1; next }
  $2 == "METADATA" { metadata += 1; next }
  { exit 1 }
  END {
    if (database != 1 || app_data != 1 || privacy_data != 1 || metadata != 1) exit 1
  }
' "$CANONICAL_BACKUP/SHA256SUMS"; then
  echo "SHA256SUMS must name each expected backup file exactly once." >&2
  exit 1
fi

if command -v sha256sum >/dev/null 2>&1; then
  (cd "$CANONICAL_BACKUP" && sha256sum --check SHA256SUMS)
else
  (cd "$CANONICAL_BACKUP" && shasum -a 256 --check SHA256SUMS)
fi

metadata_value() {
  local key="$1"
  local count
  count="$(awk -v key="$key" 'index($0, key "=") == 1 { count += 1 } END { print count + 0 }' \
    "$CANONICAL_BACKUP/METADATA")"
  [[ "$count" == "1" ]] || {
    echo "Backup metadata key must occur exactly once: $key" >&2
    exit 1
  }
  awk -v key="$key" 'index($0, key "=") == 1 { print substr($0, length(key) + 2); exit }' \
    "$CANONICAL_BACKUP/METADATA" | tr -d '\r'
}

[[ "$(metadata_value format)" == "1" ]] \
  || { echo "Unsupported backup metadata format." >&2; exit 1; }
[[ "$(metadata_value database)" == "postgresql" ]] \
  || { echo "Backup is not a PostgreSQL backup." >&2; exit 1; }
[[ "$(metadata_value database_name)" == "sixsentences" ]] \
  || { echo "Backup targets a different database name." >&2; exit 1; }
[[ "$(metadata_value includes)" == "database,app-data,privacy-data" ]] \
  || { echo "Backup metadata does not declare the complete state set." >&2; exit 1; }

validate_archive() {
  local archive="$1"
  local listing verbose_listing
  if ! listing="$(tar -tzf "$archive")"; then
    echo "Archive cannot be listed: $archive" >&2
    exit 1
  fi
  if ! verbose_listing="$(tar -tvzf "$archive")"; then
    echo "Archive metadata cannot be listed: $archive" >&2
    exit 1
  fi
  if printf '%s\n' "$verbose_listing" \
    | awk '$1 !~ /^[-d]/ { found=1 } END { exit found ? 0 : 1 }'; then
    echo "Archive contains a link or special file: $archive" >&2
    exit 1
  fi
  while IFS= read -r member; do
    [[ -n "$member" ]] || continue
    case "$member" in
      /*|../*|*/../*|*/..) echo "Archive contains an unsafe path: $member" >&2; exit 1 ;;
    esac
  done <<<"$listing"
}

PRIVACY_ARCHIVE_HAS_JOURNAL=0
validate_privacy_archive() {
  local archive="$1"
  local listing member
  if ! listing="$(tar -tzf "$archive")"; then
    echo "Privacy archive cannot be listed: $archive" >&2
    exit 1
  fi
  while IFS= read -r member; do
    [[ -n "$member" ]] || continue
    case "$member" in
      .|./) ;;
      erasure-ledger.jsonl|./erasure-ledger.jsonl) PRIVACY_ARCHIVE_HAS_JOURNAL=1 ;;
      *) echo "Privacy archive contains an unexpected entry: $member" >&2; exit 1 ;;
    esac
  done <<<"$listing"
}

RESTORE_PHASE=archive_validation
validate_archive "$CANONICAL_BACKUP/app-data.tar.gz"
validate_archive "$CANONICAL_BACKUP/privacy-data.tar.gz"
validate_privacy_archive "$CANONICAL_BACKUP/privacy-data.tar.gz"

COMPOSE=(docker compose --env-file "$ENV_FILE" --file "$COMPOSE_FILE")
LOCK_DIR="$CANONICAL_ROOT/.sixsentences-state-operation.lock"
if ! mkdir -m 700 "$LOCK_DIR" 2>/dev/null; then
  echo "Another backup or restore owns $LOCK_DIR; refusing to overlap state operations." >&2
  exit 1
fi
RESTORE_PHASE=staging
RESTORE_STAGE="$(mktemp -d "$CANONICAL_ROOT/.restore-stage.XXXXXX")"
cleanup() {
  rm -rf -- "$RESTORE_STAGE" 2>/dev/null || true
  rmdir -- "$LOCK_DIR" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM

mkdir -m 700 "$RESTORE_STAGE/candidate"
tar -xzf "$CANONICAL_BACKUP/privacy-data.tar.gz" \
  --no-same-owner --no-same-permissions -C "$RESTORE_STAGE/candidate"

JOURNAL_INPUT=/dev/null
if [[ "$PRIVACY_ARCHIVE_HAS_JOURNAL" == "1" ]]; then
  [[ -f "$RESTORE_STAGE/candidate/erasure-ledger.jsonl" \
    && ! -L "$RESTORE_STAGE/candidate/erasure-ledger.jsonl" \
    && -r "$RESTORE_STAGE/candidate/erasure-ledger.jsonl" ]] || exit 1
  JOURNAL_INPUT="$RESTORE_STAGE/candidate/erasure-ledger.jsonl"
fi
JOURNAL_HELPER="$(<"$SCRIPT_DIR/restore_journals.py")"
[[ -n "$JOURNAL_HELPER" ]] || exit 1

inspect_journals() {
  # Host-owned mode-0600 staging bytes cross stdin, not a bind-mounted path.
  # The normal API user verifies private tempfiles and its own live journal.
  "${COMPOSE[@]}" run --rm --no-deps -T --entrypoint python api \
    -c "$JOURNAL_HELPER" "$1" <"$JOURNAL_INPUT"
}

# Authenticate the backup journal before taking down a healthy deployment.
RESTORE_PHASE=candidate_journal_verification
inspect_journals verify >/dev/null

RESTORE_PHASE=stop_services
"${COMPOSE[@]}" stop --timeout 30 proxy web >/dev/null
"${COMPOSE[@]}" stop --timeout 310 worker >/dev/null
"${COMPOSE[@]}" stop --timeout 30 api >/dev/null

# With the API stopped the live journal is immutable. Verify both chains and
# choose only when one is a byte-for-byte prefix of the other. A divergent pair
# cannot be merged safely and therefore aborts before any persistent state is
# replaced.
RESTORE_PHASE=live_journal_selection
LEDGER_SOURCE="$(inspect_journals select)"
case "$LEDGER_SOURCE" in
  current|backup) ;;
  *) echo "Could not select a safe erasure journal." >&2; exit 1 ;;
esac

"${COMPOSE[@]}" up --detach --wait --wait-timeout 180 postgres

RESTORE_PHASE=database_restore
echo "Checksums and archive paths verified. Replacing persistent state..."
if [[ "$LEDGER_SOURCE" == "backup" ]]; then
  "${COMPOSE[@]}" run --rm --no-deps -T --entrypoint sh api -eu -c '
    find /privacy -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
    tar -xzf - --no-same-owner --no-same-permissions -C /privacy
  ' <"$CANONICAL_BACKUP/privacy-data.tar.gz"
fi

"${COMPOSE[@]}" exec -T postgres sh -eu -c '
  dropdb --if-exists --force --username "$POSTGRES_USER" "$POSTGRES_DB"
  createdb --username "$POSTGRES_USER" --owner "$POSTGRES_USER" "$POSTGRES_DB"
'
"${COMPOSE[@]}" exec -T postgres pg_restore \
  --exit-on-error --no-owner --no-privileges \
  --username sixsentences --dbname sixsentences \
  <"$CANONICAL_BACKUP/database.dump"

RESTORE_PHASE=application_restore
"${COMPOSE[@]}" run --rm --no-deps -T --entrypoint sh api -eu -c '
  find /data -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
  tar -xzf - --no-same-owner --no-same-permissions -C /data
' <"$CANONICAL_BACKUP/app-data.tar.gz"

# Reapply every authenticated deletion to the restored database and files while
# the public proxy and write processes are still stopped. Any verification,
# identity-guard or cleanup failure leaves the deployment closed.
RESTORE_PHASE=erasure_replay
"${COMPOSE[@]}" run --rm --no-deps -T migrate
"${COMPOSE[@]}" run --rm --no-deps -T \
  --entrypoint six-community-erasure api replay

# The API requires a live worker; start them together only after erasure replay.
RESTORE_PHASE=service_restart
"${COMPOSE[@]}" up --detach --wait --wait-timeout 180 postgres api worker
"${COMPOSE[@]}" up --detach --wait --wait-timeout 180 web proxy

RESTORE_PHASE=cleanup
rm -rf -- "$RESTORE_STAGE"
rmdir -- "$LOCK_DIR"
trap - EXIT HUP INT TERM

echo "Restore completed and service health checks passed."
echo "Verify sign-in and one read-only workspace before accepting new writes."
