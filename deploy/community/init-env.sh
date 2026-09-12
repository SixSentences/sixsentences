#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
REPOSITORY_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd -P)"
MODE="local"
DOMAIN=""
OUTPUT="$REPOSITORY_ROOT/.env.selfhost"

usage() {
  cat <<'EOF'
Usage:
  init-env.sh --local [--output PATH]
  init-env.sh --domain HOSTNAME [--output PATH]

Creates a mode-0600 environment file with fresh local database, connector and
erasure-journal keys. It never overwrites an existing file and never prints a
generated value.
EOF
}

while (($#)); do
  case "$1" in
    --local)
      MODE="local"
      DOMAIN=""
      shift
      ;;
    --domain)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      MODE="tls"
      DOMAIN="$2"
      shift 2
      ;;
    --output)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      OUTPUT="$2"
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

command -v openssl >/dev/null 2>&1 || {
  echo "openssl is required to generate deployment secrets." >&2
  exit 1
}

if [[ "$MODE" == "tls" ]]; then
  if [[ ! "$DOMAIN" =~ ^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?$ \
    || "$DOMAIN" != *.* \
    || "$DOMAIN" == *..* ]]; then
    echo "--domain must be one credential-free hostname without a scheme, port or path." >&2
    exit 2
  fi
  SITE_ADDRESS="$DOMAIN"
  PUBLIC_ORIGIN="https://$DOMAIN"
else
  SITE_ADDRESS="http://localhost"
  PUBLIC_ORIGIN="http://localhost"
fi

if [[ -e "$OUTPUT" || -L "$OUTPUT" ]]; then
  echo "Refusing to overwrite existing environment file: $OUTPUT" >&2
  exit 1
fi
OUTPUT_PARENT="$(dirname -- "$OUTPUT")"
[[ -d "$OUTPUT_PARENT" ]] || {
  echo "Output directory does not exist: $OUTPUT_PARENT" >&2
  exit 1
}

umask 077
TEMP_FILE="$(mktemp "$OUTPUT.tmp.XXXXXX")"
cleanup() {
  rm -f -- "$TEMP_FILE"
}
trap cleanup EXIT HUP INT TERM

POSTGRES_SECRET="$(openssl rand -hex 32)"
CONNECTOR_SECRET="$(openssl rand -base64 32 | tr '/+' '_-' | tr -d '\n')"
ERASURE_SECRET="$(openssl rand -hex 32)"

{
  printf '%s\n' '# Generated locally. Keep this file outside version control.'
  printf 'SIX_DEPLOYMENT_MODE=%s\n' "$MODE"
  if [[ "$MODE" == "local" ]]; then
    printf '%s\n' 'SIX_ALLOW_INSECURE_LOCAL_HTTP=true'
  else
    printf '%s\n' 'SIX_ALLOW_INSECURE_LOCAL_HTTP=false'
  fi
  printf 'SIX_SITE_ADDRESS=%s\n' "$SITE_ADDRESS"
  printf 'SIX_PUBLIC_ORIGIN=%s\n' "$PUBLIC_ORIGIN"
  printf 'SIX_PUBLIC_API_URL=%s/api\n' "$PUBLIC_ORIGIN"
  printf 'SIX_LEGAL_BASE_URL=%s/legal\n' "$PUBLIC_ORIGIN"
  printf '%s\n' 'SIX_HTTP_PORT=80' 'SIX_HTTPS_PORT=443'
  printf 'POSTGRES_PASSWORD=%s\n' "$POSTGRES_SECRET"
  printf 'SIX_CONNECTOR_ENCRYPTION_KEY=%s\n' "$CONNECTOR_SECRET"
  printf 'SIX_ERASURE_LEDGER_HMAC_KEY=%s\n' "$ERASURE_SECRET"
  printf '%s\n' \
    'SIX_TERMS_VERSION=community-1' \
    'SIX_PRIVACY_VERSION=community-1' \
    'SIX_DPA_VERSION=community-1' \
    'SIX_GOOGLE_OAUTH_CLIENT_ID=' \
    'SIX_BROWSER_CAPTURE_REDIRECT_URIS=' \
    'SIX_SELF_SIGNUP=false' \
    'SIX_REQUIRE_EMAIL_VERIFICATION=true' \
    'SIX_ENFORCE_LEGAL_ACCEPTANCE=true' \
    'SIX_SMTP_HOST=' \
    'SIX_SMTP_PORT=587' \
    'SIX_SMTP_USERNAME=' \
    'SIX_SMTP_PASSWORD=' \
    'SIX_SMTP_STARTTLS=true' \
    'SIX_MAIL_FROM=community@example.invalid' \
    'SIX_MAIL_REPLY_TO=community@example.invalid' \
    'SIX_OPENALEX_API_KEY=' \
    'SIX_OPENALEX_MAILTO=researcher@example.invalid' \
    'SIX_GROBID_URL=' \
    'SIX_OPENROUTER_API_KEY=' \
    'SIX_OPENROUTER_BASE_URL=https://model-provider.example.invalid/v1' \
    'SIX_WEBSEARCH_API_KEY=' \
    'SIX_WEBSEARCH_DATA_PROCESSING_CONFIRMED=false' \
    'SIX_LLM_ROUTING=' \
    'SIX_LLM_BUDGET_USD=10' \
    'SIX_GEMINI_API_KEY=' \
    'SIX_GEMINI_PAID_SERVICE_CONFIRMED=false' \
    'SIX_PUBLIC_SPOKEN_INTERVIEWS_ENABLED=false' \
    'SIX_WORKER_CONCURRENCY=2' \
    'SIX_STORAGE_RESERVE_BYTES=1073741824' \
    'SIX_REPOSITORY_ANALYSIS_ENABLED=true' \
    'SIX_BACKUP_DIR=/var/backups/sixsentences-community'
} >"$TEMP_FILE"

chmod 600 "$TEMP_FILE"
mv -- "$TEMP_FILE" "$OUTPUT"
trap - EXIT HUP INT TERM
unset POSTGRES_SECRET CONNECTOR_SECRET ERASURE_SECRET
echo "Created $OUTPUT with mode 0600. Generated values were not printed."
