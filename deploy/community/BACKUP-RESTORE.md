# Backup and restore

The reference scripts create a cold, consistent application backup with brief
API downtime. A backup contains:

- a custom-format logical PostgreSQL dump;
- the application-data volume (documents, corpora and generated workspace
  artifacts);
- the separate authenticated erasure journal; and
- non-secret format/revision and container-image identity metadata plus SHA-256
  checksums.

Caddy certificates and container images are not backed up. Certificates are
renewed from configuration; rebuild images from the recorded public source
revision. The private `.env.selfhost` file is also deliberately excluded. Keep
an independently encrypted recovery copy of it: the erasure journal cannot be
authenticated without its HMAC key, and encrypted connector credentials cannot
be recovered without their separate key. Never place that copy beside the data
backup under the same credentials.

## Create a backup

Set `SIX_BACKUP_DIR` in `.env.selfhost` to an absolute directory outside the
checkout, owned by the deployment administrator. Then run:

```console
bash deploy/community/backup.sh
```

The script validates configuration, records which write services were running,
drains the worker, stops the API, dumps PostgreSQL, rejects links and special
files in persistent volumes, creates checksums, atomically publishes the
timestamped directory and health-checks only the write services that were
previously running. Backup and restore share an atomic state-operation lock and
refuse to overlap.

Immediately copy the completed directory to separately encrypted storage with
independent credentials. A local checksum detects accidental corruption; it is
not an authenticity signature and does not make a same-host copy disaster-safe.
Choose retention that also honors participant deletion and consent withdrawal
obligations.

## Restore

Use the same source revision and image versions recorded in `METADATA`. First
create a fresh safety backup if the current state is still valuable. Then:

```console
bash deploy/community/restore.sh \
  --backup /absolute/backup/root/20260912T120000Z \
  --confirm RESTORE
```

Restore is intentionally destructive. It requires an exact confirmation,
accepts only a non-symlink directory below the configured backup root, verifies
every checksum, rejects link, special-file and traversal entries, and verifies
the backup erasure journal before stopping public/write services. It then
authenticates the live journal and accepts the two only when one is an exact
prefix of the other. The longer chain is retained, so deletions recorded after
the data snapshot cannot be lost. Divergent journals fail closed.

The script recreates the database, replaces application files and replays the
selected authenticated journal against both restored state stores before the
API, worker or proxy can reopen. A new host receives the journal from the
archive; a rollback on an existing host normally keeps its newer dedicated
privacy volume. If any destructive step or replay identity guard fails, public
and write services remain stopped for inspection. On success, Compose waits for
the database, API, worker, web and proxy health checks.

Afterward, verify account sign-in, a representative read-only project, document
download and one queued background job before allowing new writes. Retain the
JSON replay summary with the restore-drill record; it contains counts and chain
metadata, not participant content.

An unclean host shutdown can leave
`.sixsentences-state-operation.lock` inside `SIX_BACKUP_DIR`. First confirm that
no backup or restore process is running and that public/write services are in a
known state; only then remove that empty directory and rerun the operation.

## Restore drill

At least quarterly, restore a recent encrypted copy on an isolated host using a
different DNS name and no external-provider credentials. Record duration,
checksum result, database migration revision, file counts, sign-in result,
worker result and deletion-journal reconciliation. Never treat an untested
archive as a working backup.
