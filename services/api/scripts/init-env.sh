#!/bin/sh
set -eu

if [ -e .env ]; then
  printf '%s\n' '.env already exists; refusing to overwrite it.' >&2
  exit 1
fi

umask 077
database_password=$(openssl rand -hex 24)
connector_key=$(openssl rand -base64 32 | tr '+/' '-_' | tr -d '\n')
erasure_key=$(openssl rand -hex 32)

sed \
  -e "s/CHANGE_ME_DATABASE_PASSWORD/$database_password/g" \
  -e "s/CHANGE_ME_FERNET_KEY/$connector_key/g" \
  -e "s/CHANGE_ME_ERASURE_HMAC_KEY/$erasure_key/g" \
  .env.example > .env

unset database_password connector_key erasure_key
printf '%s\n' 'Created .env with mode 0600. No secret was printed.'
