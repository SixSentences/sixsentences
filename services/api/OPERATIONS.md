# Community operations and recovery

The Compose stack owns three independent state classes:

- PostgreSQL contains accounts, projects, research metadata and job state.
- `/data` contains the search corpus and user-created or acquired files.
- `/privacy/erasure-ledger.jsonl` is a tamper-evident, content-free journal of
  deletions that happened after an older snapshot. Its HMAC key is supplied by
  `SIX_ERASURE_LEDGER_HMAC_KEY` and is not stored in the journal.

An operator backup is complete only when it includes a consistent PostgreSQL
dump, `/data`, the newest independently held `/privacy` copy, and an encrypted
copy of the runtime configuration. Do not store the configuration or HMAC key in
the source repository, image, application logs, or an unencrypted backup. Choose
backup storage, retention and geographic location for your own deployment and
document them in the participant/privacy information shown by that deployment.

The bundled PostgreSQL workflow captures those three state classes while the
write processes are stopped:

```text
./deploy/community/backup.sh --destination /absolute/encrypted-backup-volume
```

The command publishes a timestamped directory only after the database dump,
archives and exact checksum inventory validate. Keep the `.env` file as a
separate encrypted recovery input; it is intentionally never copied into the
backup directory.

## Recovery invariant

Never restore directly over a serving instance. Point an isolated replacement
checkout at a selected backup and run:

```text
./deploy/community/restore.sh \
  --backup /absolute/encrypted-backup-volume/20260912T120000Z \
  --confirm RESTORE
```

The script validates paths, file types, the exact checksum inventory and safe
archive members before stopping writes. It then follows this order:

1. Keep the API and worker stopped.
2. Restore PostgreSQL and `/data` from the same recovery point.
3. Restore the newest independently held erasure ledger and its matching HMAC key.
4. Run `docker compose run --rm api six-community-erasure verify`.
5. Run `docker compose run --rm api six-community-erasure replay`. This reapplies
   every authenticated post-snapshot workspace/member deletion idempotently.
6. Run `docker compose run --rm migrate` and
   `docker compose run --rm api six-community corpus verify`.
7. Start `api` and `worker`; require HTTP 200 from `/health/ready` before routing
   traffic to the replacement.

Treat any signature-chain failure, identity-guard mismatch, missing independent
ledger, or failed corpus/database verification as a hard stop. The replay report
contains counts and signatures but no email address or research content. Preserve
it with the recovery record; do not use it as a substitute for the ledger.

The restore script authenticates both erasure chains before selecting the longer
byte-compatible journal. Divergent chains fail closed. It executes migration,
erasure replay, corpus verification and API/worker health checks before reporting
success; a failed restore leaves both write processes stopped.

The lower-level `six-community-backup snapshot/verify` commands are intentionally
limited to SQLite deployments and use SQLite's online backup API. Practice a full
isolated restore periodically; a successful backup command alone is not recovery
evidence.
