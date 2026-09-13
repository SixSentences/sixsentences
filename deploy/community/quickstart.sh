#!/usr/bin/env bash
set -euo pipefail

# One command from a fresh checkout to a workspace you can sign in to.
#
# Every step is the documented command it replaces — configuration, the
# fail-closed preflight, images, a health-gated start, and the first owner
# account — so running them by hand still produces the same deployment. The
# script adds no configuration of its own, prints no secret value, and stops at
# the first failure instead of leaving a half-started stack behind.

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
REPOSITORY_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd -P)"
ENV_FILE="$REPOSITORY_ROOT/.env.selfhost"
MODE="local"
DOMAIN=""
IMAGE_SOURCE="auto"
CREATE_OWNER="true"

usage() {
  cat <<'EOF'
Usage:
  quickstart.sh [--local | --domain HOSTNAME] [options]

Options:
  --local              loopback-only HTTP deployment (default)
  --domain HOSTNAME    public TLS deployment for HOSTNAME
  --env-file PATH      environment file to create or reuse (default .env.selfhost)
  --build              build the images from this checkout
  --pull               pull the images named by SIX_API_IMAGE and SIX_WEB_IMAGE
  --no-owner           skip the first-owner prompt and print the command instead
  -h, --help           show this help

Without --build or --pull the script pulls when both image variables are set
and builds otherwise. It never overwrites an existing environment file.
EOF
}

fail() {
  echo "Quick start failed: $*" >&2
  exit 1
}

step() {
  printf '\n==> %s\n' "$*"
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
    --env-file)
      [[ $# -ge 2 ]] || { usage >&2; exit 2; }
      ENV_FILE="$2"
      shift 2
      ;;
    --build)
      IMAGE_SOURCE="build"
      shift
      ;;
    --pull)
      IMAGE_SOURCE="pull"
      shift
      ;;
    --no-owner)
      CREATE_OWNER="false"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      exit 2
      ;;
  esac
done

# Argument errors are reported before anything is created or started.
if [[ "$IMAGE_SOURCE" == "pull" ]]; then
  [[ -n "${SIX_API_IMAGE:-}" && -n "${SIX_WEB_IMAGE:-}" ]] \
    || fail "--pull needs SIX_API_IMAGE and SIX_WEB_IMAGE to name published images"
fi
if [[ "$MODE" == "tls" && -z "$DOMAIN" ]]; then
  fail "--domain needs a hostname"
fi

command -v docker >/dev/null 2>&1 || fail "docker is required"

compose() {
  docker compose --env-file "$ENV_FILE" --file "$REPOSITORY_ROOT/compose.yaml" "$@"
}

step "Configuration"
if [[ -e "$ENV_FILE" ]]; then
  echo "Reusing the existing environment file. Delete it to start from scratch."
elif [[ "$MODE" == "tls" ]]; then
  bash "$SCRIPT_DIR/init-env.sh" --domain "$DOMAIN" --output "$ENV_FILE"
  echo "Review the sender, legal-document location and provider settings before"
  echo "you accept real users: $ENV_FILE"
else
  bash "$SCRIPT_DIR/init-env.sh" --local --output "$ENV_FILE"
fi

step "Preflight"
bash "$SCRIPT_DIR/preflight.sh" "$ENV_FILE"

if [[ "$IMAGE_SOURCE" == "auto" ]]; then
  if [[ -n "${SIX_API_IMAGE:-}" && -n "${SIX_WEB_IMAGE:-}" ]]; then
    IMAGE_SOURCE="pull"
  else
    IMAGE_SOURCE="build"
  fi
fi

if [[ "$IMAGE_SOURCE" == "pull" ]]; then
  step "Pulling published images"
  echo "The web image carries the public origin it was built for; a pulled image"
  echo "only serves the deployment mode it was published for."
  compose pull --quiet postgres api worker web proxy
else
  step "Building images from this checkout"
  echo "This is a full application build and takes a while on a cold cache."
  compose build
fi

step "Starting the stack"
compose up --detach --wait

step "Readiness"
if ! compose exec -T api python -c 'import urllib.request; urllib.request.urlopen("http://127.0.0.1:8000/health/ready", timeout=10).read()' >/dev/null 2>&1; then
  fail "the API did not report ready; inspect 'docker compose logs api'"
fi
echo "The API reports ready and the database migrations have been applied."

PUBLIC_ORIGIN="$(awk 'index($0, "SIX_PUBLIC_ORIGIN=") == 1 { print substr($0, 19); exit }' "$ENV_FILE" | tr -d '\r')"

OWNER_COMMAND="docker compose --env-file $ENV_FILE run --rm api six-community auth create-owner --email you@example.org --org \"My Lab\""
if [[ "$CREATE_OWNER" != "true" ]]; then
  step "First owner"
  echo "Create the first account yourself:"
  echo "  $OWNER_COMMAND"
elif [[ ! -t 0 ]]; then
  step "First owner"
  echo "Self-signup is off by default, and this run has no terminal to prompt on."
  echo "Create the first account with:"
  echo "  $OWNER_COMMAND"
else
  step "First owner"
  echo "Self-signup is off by default, so the first account is created here. The"
  echo "password is prompted twice and never appears in a command line or log."
  read -r -p "Owner email (empty to skip): " OWNER_EMAIL
  if [[ -n "$OWNER_EMAIL" ]]; then
    read -r -p "Workspace name [My Lab]: " OWNER_ORG
    read -r -p "Owner first name (optional): " OWNER_FIRST_NAME
    compose run --rm api six-community auth create-owner \
      --email "$OWNER_EMAIL" \
      --org "${OWNER_ORG:-My Lab}" \
      --first-name "$OWNER_FIRST_NAME" \
      || echo "The owner was not created. Re-run: $OWNER_COMMAND"
  else
    echo "Skipped. Create it later with:"
    echo "  $OWNER_COMMAND"
  fi
fi

step "Ready"
echo "Open ${PUBLIC_ORIGIN:-the public origin from $ENV_FILE} and sign in."
cat <<EOF

  Status   docker compose --env-file $ENV_FILE ps
  Logs     docker compose --env-file $ENV_FILE logs --tail 100 api worker web proxy
  Stop     docker compose --env-file $ENV_FILE down
  Backup   bash deploy/community/backup.sh $ENV_FILE

Read deploy/community/README.md before enabling registration, mail, external
models, web search or spoken interviews, and the backup and restore runbook
before storing research data.
EOF
