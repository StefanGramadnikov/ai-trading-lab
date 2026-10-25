# 26 — Local backup, isolated restore and rollback

M8A implementation tooling only. No scheduled backup, data retention, public port,
cloud activation, multi-day run, paid AI or Telegram send is authorized here.
A PostgreSQL archive contains private operational evidence: keep it outside Git,
in an owner-only directory on trusted local storage. Backups are not encrypted by
this tool. Preserve the installed image digest/code checkout, original immutable
configurations and source captures with the archive; the manifest's installed
Python fingerprint is not a substitute for preserving the original image.

## Explicit bounded invocation

Stop all workers and other database writers before backup; API reporting may remain
read-only. Do not migrate or change configurations during backup/verification.
Use the existing PostgreSQL 17.9 bookworm container's `pg_dump`, `pg_restore`, `psql`
and GNU `timeout`; no additional service or dependency is installed. The host needs
Docker access and the locked Python environment. Identify the intended existing
container with `docker compose -p atl-m1-validation ps -q db` and pass its ID to
`--container`. Do not point at an unrelated container.

Configure `ATL_DATABASE_URL` securely in the local ignored environment, using the
source database and a host/port reachable **inside the PostgreSQL container**. For
that container itself this is `127.0.0.1:5432`; the published host port such as
55432 is not the container's PostgreSQL port. Never paste a password-bearing URL
into shell arguments, logs or chat. Existing `Settings` loads the local environment.
The URL must be a plain `postgresql+psycopg` URL without query options. Credentials
travel through PostgreSQL environment variables; command arguments forward only
their names to Docker. Local Docker administrators can inspect process/container
environments, so this is not protection from a privileged local administrator.

After setting the local environment, substitute the actual container ID below:

```bash
uv run --locked atl-backup backup /private/tmp/atl-backup-example --container CONTAINER_ID --timeout 60 --max-bytes 67108864
uv run --locked atl-backup restore /private/tmp/atl-backup-example --container CONTAINER_ID --database ai_trading_lab_restore_test --timeout 60 --max-bytes 67108864
```

`python -m ai_trading_lab.operations.backup` accepts the same arguments. The output
contains manifest/count evidence, never the connection URL or subprocess stderr.
Both commands are explicit user-controlled operations; examples do not activate a
schedule. Each subprocess is bounded to at most the configured timeout (1–300s)
and output cap; a complete backup/restore has several sequential subprocesses.
Archive cap is 1 KiB–1 GiB; metadata/query output is capped at 64 KiB. The host
kills a timed-out Docker client; the container's `timeout --signal=KILL` separately
bounds the PostgreSQL child. A failed restore may leave its freshly created test
database, deliberately preserved for inspection.

## Artifact and verification contract

Backup exclusively creates a new mode-0700 directory, refuses existing destinations
and symlink components, and writes mode-0600 files. The custom-format archive is
first streamed into `archive.partial`, checked for its `PGDMP` header, flushed and
renamed to `archive.dump`; `manifest.json` records its SHA-256, size, UTC creation
time, source database name, installed Python fingerprint, migration revision(s)
and basic counts. Exceptions remove only this invocation's incomplete artifacts.
Source queries before and after dump must agree. These queries are not a shared
PostgreSQL snapshot: **stopping writers is required**, and equal counts alone do
not prove arbitrary concurrent data changes were absent. `pg_dump` itself supplies
a consistent database archive.

Verification checks the owner, strict directory/file permissions, regular files,
byte bounds and checksum before touching PostgreSQL. It refuses any restore target
except the literal `ai_trading_lab_restore_test` on the same configured server.
It queries existence and then issues a non-replacing `CREATE DATABASE ... TEMPLATE
template0`; PostgreSQL itself rejects a concurrent create. It never issues DROP,
CLEAN or a restore over an existing database. Restore is one transaction with
owner/privilege restoration disabled. A failure preserves the archive and created
database; it must not delete a potentially useful investigation artifact.

The returned summary must equal the saved migrations and counts for raw source
events, paper portfolios/events, experiments and runtime sessions/events. This
requires the M8A schema already migrated. It verifies schema/count preservation,
not a complete application financial replay or the trustworthiness of an archive.
Restore only archives generated and retained by the operator; PostgreSQL archives
can contain executable schema definitions. Before any subsequent operational use,
run the existing application replay/reconciliation commands from the **original
preserved code/image** against the isolated database. Do not migrate the archive
or reinterpret historical reports with newer code to make them appear compatible.
A checksum is integrity evidence, not authentication against malicious replacement.

## Startup, migration and rollback sequence

1. Keep source worker stopped; record image digest, installed source fingerprint,
   migration head, explicit config/source identity and last durable checkpoint.
2. Back up the quiescent source into a new private directory. Retain existing
   archives; this tool has no pruning or overwrite policy.
3. Verify isolated restore and replay before changing the source schema/image.
   Existing test database refusal requires manual investigation. This tool never
   deletes it; cleanup is a separate explicitly approved operator action.
4. Apply only reviewed additive migrations to the intended database, then start
   the same pinned image/config and verify health plus runtime reconciliation.
   API readiness alone is not paper-trading readiness.
5. If startup/reconciliation fails, keep workers stopped and preserve diagnostic
   evidence. Roll back to the preserved compatible image against compatible data;
   never silently downgrade an applied schema or overwrite the source database.
   A source database replacement/production restoration needs a separately
   reviewed recovery plan and explicit approval. The isolated helper cannot do it.

Record actual command outcomes, archive identity and restored replay results in
the progress log only after they run. Unit-test stubs demonstrate command/path/
resource guard behavior; they are not evidence of an actual PostgreSQL restore.
