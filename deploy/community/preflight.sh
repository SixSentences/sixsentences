#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
REPOSITORY_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd -P)"
COMPOSE_FILE="$REPOSITORY_ROOT/compose.yaml"
ENV_FILE="${1:-$REPOSITORY_ROOT/.env.selfhost}"

fail() {
  echo "Self-host preflight failed: $*" >&2
  exit 1
}

[[ -f "$ENV_FILE" && ! -L "$ENV_FILE" ]] || fail "environment file is missing or a symlink: $ENV_FILE"
[[ -f "$COMPOSE_FILE" && ! -L "$COMPOSE_FILE" ]] || fail "compose file is missing or a symlink"

mode_bits=""
if stat -f '%Lp' "$ENV_FILE" >/dev/null 2>&1; then
  mode_bits="$(stat -f '%Lp' "$ENV_FILE")"
else
  mode_bits="$(stat -c '%a' "$ENV_FILE")"
fi
case "$mode_bits" in
  600|400) ;;
  *) fail "environment file permissions must be 0600 or 0400 (found $mode_bits)" ;;
esac

# Keep physical lines unambiguous before inspecting individual fields: a
# guarded assignment must never be swallowed by another field's quoted value.
LC_ALL=C tr -d '\000' < "$ENV_FILE" | cmp -s - "$ENV_FILE" \
  || fail "environment file must not contain NUL bytes"
LC_ALL=C awk '
  {
    line = $0
    sub(/\r$/, "", line)
    if (line ~ /[\001-\010\013-\037\177]/) { invalid = 1; next }
    if (line ~ /^[ \t]*$/ || line ~ /^[ \t]*#/) next
    if (line !~ /^[A-Za-z_][A-Za-z0-9_]*=/) { invalid = 1; next }
    value = substr(line, index(line, "=") + 1)
    sub(/^[ \t]+/, "", value)
    quote = substr(value, 1, 1)
    if (quote == "\"" || quote == sprintf("%c", 39)) {
      closed = 0
      for (position = 2; position <= length(value); position++) {
        character = substr(value, position, 1)
        if (character == "\\") { position++; continue }
        if (character == quote) { closed = position; break }
      }
      if (!closed || substr(value, closed + 1) !~ /^[ \t]*(#.*)?$/) invalid = 1
    } else if ((length(value) && value !~ /^[ -~]/) ||
               substr(value, length(value), 1) == "\\") {
      invalid = 1
    }
  }
  END { exit (invalid ? 1 : 0) }
' "$ENV_FILE" || fail "environment file requires single-line KEY=value assignments; multiline values and alternate assignment syntax are not supported"

command -v printenv >/dev/null 2>&1 || fail "printenv is required to check inherited settings"

env_value() {
  local key="$1"
  local count
  count="$(awk -v key="$key" 'index($0, key "=") == 1 { count += 1 } END { print count + 0 }' "$ENV_FILE")"
  [[ "$count" == "1" ]] || fail "$key must occur exactly once"
  awk -v key="$key" 'index($0, key "=") == 1 {
    value = substr($0, length(key) + 2)
    sub(/\r$/, "", value)
    printf "%s", value
    exit
  }' "$ENV_FILE"
}

validate_guarded_value() {
  local key="$1"
  local value
  # These fields are also read literally by deployment helpers. Do not allow
  # Compose interpolation or alternate declarations to change the checked value.
  LC_ALL=C awk -v key="$key" '
    {
      line = $0
      sub(/\r$/, "", line)
      declaration = line
      sub(/^[[:space:]]+/, "", declaration)
      sub(/^export[[:space:]]+/, "", declaration)
      if (declaration ~ ("^" key "([[:space:]]*[:=]|[[:space:]]*(#.*)?$)")) {
        declarations += 1
      }
      if (index(line, key "=") == 1) {
        canonical += 1
        value = substr(line, length(key) + 2)
        if (value ~ /[^ -~]/ || value ~ /^[[:space:]]/ ||
            value ~ /[[:space:]]$/ || index(value, " #") ||
            index(value, "$") || index(value, "\\") ||
            index(value, "\"") || index(value, sprintf("%c", 39))) {
          invalid = 1
        }
      }
    }
    END { exit (declarations != 1 || canonical != 1 || invalid) }
  ' "$ENV_FILE" || fail "$key requires exactly one literal ASCII KEY=value line; remove alternate assignments, quotes, interpolation, comments, or surrounding whitespace"
  value="$(env_value "$key")"
  # Command substitution would strip trailing newlines from an inherited value.
  # Indirect expansion preserves its exact bytes, including an exported empty value.
  if printenv "$key" >/dev/null 2>&1; then
    [[ "${!key}" == "$value" ]] \
      || fail "$key differs from the environment file; unset the shell override or update the file"
  fi
}

for guarded_key in \
  SIX_DEPLOYMENT_MODE SIX_ALLOW_INSECURE_LOCAL_HTTP SIX_SITE_ADDRESS \
  SIX_PUBLIC_ORIGIN SIX_PUBLIC_API_URL SIX_LEGAL_BASE_URL POSTGRES_PASSWORD \
  SIX_CONNECTOR_ENCRYPTION_KEY SIX_ERASURE_LEDGER_HMAC_KEY SIX_BACKUP_DIR \
  SIX_SELF_SIGNUP SIX_REQUIRE_EMAIL_VERIFICATION SIX_SMTP_HOST SIX_MAIL_FROM \
  SIX_PUBLIC_SPOKEN_INTERVIEWS_ENABLED SIX_GEMINI_DATA_PROCESSING_CONFIRMED \
  SIX_GEMINI_API_KEY SIX_PUBMED_ENABLED SIX_PUBMED_EMAIL SIX_PUBMED_API_KEY; do
  validate_guarded_value "$guarded_key"
done
unset guarded_key

required_value() {
  local key="$1"
  local value
  value="$(env_value "$key")"
  [[ -n "$value" ]] || fail "$key must not be empty"
  printf '%s' "$value"
}

DEPLOYMENT_MODE="$(required_value SIX_DEPLOYMENT_MODE)"
ALLOW_INSECURE_LOCAL_HTTP="$(required_value SIX_ALLOW_INSECURE_LOCAL_HTTP)"
SITE_ADDRESS="$(required_value SIX_SITE_ADDRESS)"
PUBLIC_ORIGIN="$(required_value SIX_PUBLIC_ORIGIN)"
PUBLIC_API_URL="$(required_value SIX_PUBLIC_API_URL)"
LEGAL_BASE_URL="$(required_value SIX_LEGAL_BASE_URL)"
POSTGRES_SECRET="$(required_value POSTGRES_PASSWORD)"
CONNECTOR_SECRET="$(required_value SIX_CONNECTOR_ENCRYPTION_KEY)"
ERASURE_SECRET="$(required_value SIX_ERASURE_LEDGER_HMAC_KEY)"
BACKUP_DIR="$(required_value SIX_BACKUP_DIR)"

[[ "$POSTGRES_SECRET" =~ ^[0-9a-fA-F]{64}$ ]] \
  || fail "POSTGRES_PASSWORD must be a fresh 32-byte hex value"
[[ "$CONNECTOR_SECRET" =~ ^[A-Za-z0-9_-]{43}=?$ ]] \
  || fail "SIX_CONNECTOR_ENCRYPTION_KEY must be a URL-safe base64 encoding of 32 random bytes"
[[ "$ERASURE_SECRET" =~ ^[0-9a-fA-F]{64}$ ]] \
  || fail "SIX_ERASURE_LEDGER_HMAC_KEY must be a distinct 32-byte hex value"
[[ "$POSTGRES_SECRET" != "$ERASURE_SECRET" ]] || fail "database and journal keys must differ"
[[ "$BACKUP_DIR" == /* ]] || fail "SIX_BACKUP_DIR must be absolute and outside the checkout"

canonical_backup_directory() {
  local remaining="${1#/}"
  local resolved="/"
  local component
  # Resolve existing parents physically, but keep missing directory components
  # in memory. Preflight must not create directories or change their permissions.
  while [[ -n "$remaining" ]]; do
    component="${remaining%%/*}"
    if [[ "$remaining" == */* ]]; then
      remaining="${remaining#*/}"
    else
      remaining=""
    fi
    case "$component" in
      ""|.) ;;
      ..)
        resolved="${resolved%/*}"
        [[ -n "$resolved" ]] || resolved="/"
        ;;
      *)
        resolved="${resolved%/}/$component"
        if [[ -d "$resolved" ]]; then
          # A slash sentinel preserves trailing newlines in a physical parent
          # until they can be rejected instead of silently changing the path.
          resolved="$(CDPATH= cd -P -- "$resolved" 2>/dev/null && printf '%s/' "$PWD")" \
            || return 1
          resolved="${resolved%/}"
          [[ ! "$resolved" =~ [[:cntrl:]] ]] \
            || fail "SIX_BACKUP_DIR must not resolve through control characters"
        elif [[ -e "$resolved" || -L "$resolved" ]]; then
          return 1
        fi
        ;;
    esac
  done
  CANONICAL_BACKUP_DIR="$resolved"
}

canonical_backup_directory "$BACKUP_DIR" \
  || fail "SIX_BACKUP_DIR must resolve through valid directories"
[[ "$CANONICAL_BACKUP_DIR" != "/" ]] || fail "SIX_BACKUP_DIR must not be the filesystem root"
case "$CANONICAL_BACKUP_DIR/" in
  "$REPOSITORY_ROOT"/*) fail "SIX_BACKUP_DIR must be outside the checkout" ;;
esac

[[ "$PUBLIC_API_URL" == "$PUBLIC_ORIGIN/api" ]] \
  || fail "SIX_PUBLIC_API_URL must equal SIX_PUBLIC_ORIGIN plus /api"
[[ "$LEGAL_BASE_URL" == "$PUBLIC_ORIGIN"/* ]] \
  || fail "SIX_LEGAL_BASE_URL must share SIX_PUBLIC_ORIGIN"

case "$DEPLOYMENT_MODE" in
  tls)
    [[ "$ALLOW_INSECURE_LOCAL_HTTP" == "false" ]] \
      || fail "TLS mode must not enable the local HTTP build exception"
    [[ "$PUBLIC_ORIGIN" == https://* ]] || fail "TLS mode requires an HTTPS public origin"
    [[ "$SITE_ADDRESS" != http://* ]] || fail "TLS mode requires a Caddy hostname or HTTPS address"
    case "$PUBLIC_ORIGIN $SITE_ADDRESS $LEGAL_BASE_URL" in
      *.invalid*|*localhost*|*127.0.0.1*) fail "TLS mode still contains an example/local address" ;;
    esac
    ;;
  local)
    [[ "$ALLOW_INSECURE_LOCAL_HTTP" == "true" ]] \
      || fail "local mode requires the explicit loopback-only HTTP build exception"
    [[ "$PUBLIC_ORIGIN" =~ ^http://(localhost|127\.0\.0\.1)(:[0-9]+)?$ ]] \
      || fail "local HTTP mode is limited to localhost or 127.0.0.1"
    [[ "$SITE_ADDRESS" == "$PUBLIC_ORIGIN" ]] \
      || fail "local SIX_SITE_ADDRESS and SIX_PUBLIC_ORIGIN must match"
    ;;
  *) fail "SIX_DEPLOYMENT_MODE must be tls or local" ;;
esac

SELF_SIGNUP="$(required_value SIX_SELF_SIGNUP)"
REQUIRE_VERIFICATION="$(required_value SIX_REQUIRE_EMAIL_VERIFICATION)"
[[ "$SELF_SIGNUP" == "true" || "$SELF_SIGNUP" == "false" ]] \
  || fail "SIX_SELF_SIGNUP must be true or false"
[[ "$REQUIRE_VERIFICATION" == "true" || "$REQUIRE_VERIFICATION" == "false" ]] \
  || fail "SIX_REQUIRE_EMAIL_VERIFICATION must be true or false"
if [[ "$SELF_SIGNUP" == "true" ]]; then
  [[ "$REQUIRE_VERIFICATION" == "true" ]] || fail "public registration requires email verification"
  [[ -n "$(env_value SIX_SMTP_HOST)" ]] || fail "public registration requires SMTP"
  case "$(required_value SIX_MAIL_FROM)" in
    *@example.invalid*|*@example.test*) fail "public registration requires a real sender address" ;;
  esac
fi

SPOKEN_ENABLED="$(required_value SIX_PUBLIC_SPOKEN_INTERVIEWS_ENABLED)"
if [[ "$SPOKEN_ENABLED" == "true" ]]; then
  [[ "$(required_value SIX_GEMINI_DATA_PROCESSING_CONFIRMED)" == "true" ]] \
    || fail "spoken interviews require the provider-processing confirmation"
  [[ -n "$(env_value SIX_GEMINI_API_KEY)" ]] \
    || fail "spoken interviews require a server-side voice provider key"
elif [[ "$SPOKEN_ENABLED" != "false" ]]; then
  fail "SIX_PUBLIC_SPOKEN_INTERVIEWS_ENABLED must be true or false"
fi

PUBMED_ENABLED="$(required_value SIX_PUBMED_ENABLED)"
PUBMED_EMAIL="$(env_value SIX_PUBMED_EMAIL)"
PUBMED_API_KEY="$(env_value SIX_PUBMED_API_KEY)"
if [[ "$PUBMED_ENABLED" == "true" ]]; then
  [[ -n "$PUBMED_EMAIL" ]] || fail "PubMed retrieval requires an NCBI contact email"
elif [[ "$PUBMED_ENABLED" != "false" ]]; then
  fail "SIX_PUBMED_ENABLED must be true or false"
fi
if [[ -n "$PUBMED_EMAIL" ]] \
  && [[ ! "$PUBMED_EMAIL" =~ ^[^@[:space:]]+@[^@[:space:].]+(\.[^@[:space:].]+)+$ ]]; then
  fail "SIX_PUBMED_EMAIL must be a valid contact email address"
fi
[[ "$PUBMED_API_KEY" != *[[:space:]]* ]] \
  || fail "SIX_PUBMED_API_KEY must not contain whitespace"

FORBIDDEN_PREFIX='SIX_STR''IPE_'
if grep -Eq "^${FORBIDDEN_PREFIX}" "$ENV_FILE"; then
  fail "the community environment contains a commercial-account variable"
fi

command -v docker >/dev/null 2>&1 || fail "docker is required"
docker compose version >/dev/null 2>&1 || fail "Docker Compose v2 is required"
COMPOSE_VERSION="$(docker compose version --short 2>/dev/null)" \
  || fail "could not determine the Docker Compose version"
if [[ "$COMPOSE_VERSION" =~ ^v?([0-9]+)\.([0-9]+)(\.([0-9]+))? ]]; then
  COMPOSE_MAJOR="${BASH_REMATCH[1]}"
  COMPOSE_MINOR="${BASH_REMATCH[2]}"
  COMPOSE_PATCH="${BASH_REMATCH[4]:-0}"
else
  fail "could not parse Docker Compose version"
fi
if ((COMPOSE_MAJOR < 2)) \
  || ((COMPOSE_MAJOR == 2 && COMPOSE_MINOR < 33)) \
  || ((COMPOSE_MAJOR == 2 && COMPOSE_MINOR == 33 && COMPOSE_PATCH < 1)); then
  fail "Docker Compose 2.33.1 or newer is required for deterministic egress routing"
fi
docker compose --env-file "$ENV_FILE" --file "$COMPOSE_FILE" config --quiet >/dev/null 2>&1 \
  || fail "Docker Compose configuration validation failed; check the environment file and compose file locally"

unset POSTGRES_SECRET CONNECTOR_SECRET ERASURE_SECRET PUBMED_EMAIL PUBMED_API_KEY
echo "Self-host preflight passed. No secret values were printed."
