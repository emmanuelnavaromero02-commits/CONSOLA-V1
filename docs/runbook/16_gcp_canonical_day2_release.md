# GCP-canonical day-2 release

This is the only supported post-bootstrap release path for the single-node GCP
production environment. It deploys an already-published annotated tag; it does
not build images, move tags, change DNS, create another scheduler, promote AWS,
or read a GHCR credential on the operator machine.

## Safety model

- `origin/main`, the annotated tag, `VERSION`, and the full commit SHA must all
  identify the same release.
- The source archive is uploaded to
  `gs://$GCP_SOURCE_BUCKET/deploy-artifacts/<sha>/repo.tar.gz` with
  `ifGenerationMatch=0` and commit/checksum metadata. An existing object is
  reusable only when both values match.
- A pre-deploy backup is mandatory. Its manifest is immutable and checksum
  bound to the currently running commit and version.
- Exactly 15 proprietary images are pulled before any runtime mutation. The
  pulled tags are resolved to RepoDigests and the deployment uses a generated
  digest-only Compose lock.
- The current release directory is never overwritten. `current` is atomically
  promoted only after migrations, health, data readiness, digest checks, and
  exactly one scheduler are green.
- Application writers and the scheduler remain stopped on any failure after
  the fence. This is intentional; do not bypass it by starting containers
  manually. Diagnose, restore from the verified backup if required, and rerun
  the canonical operation.

## Private GHCR contract

The release artifact contains the audited public helper at
`infra/terraform-gcp/release/ghcr-auth-run.sh`. The day-2 script installs that
exact file atomically as:

```text
/opt/modecissions/shared/bin/ghcr-auth-run
```

The helper accepts the command to run as `"$@"`. It obtains the private pull
credential from GCP Secret Manager using the VM service account, creates an
ephemeral `DOCKER_CONFIG`, runs the command, and removes the config. The token
must never enter `infra.env`, a container, a command argument, SSH output, or
release evidence. Only these non-secret selectors cross the operator boundary:

- `OMEGA_GCP_ENVIRONMENT`
- `OMEGA_GHCR_PULL_SECRET_VERSION` (an explicit numeric version, never
  `latest`)

The packages `banxico`, `inegi`, and `sec_edgar` remain private.

## Required operator variables

```bash
export GCP_PROJECT_ID='<project-id>'
export GCP_APP_ZONE='<zone>'
export GCP_APP_INSTANCE='<instance>'
export GCP_LAKEHOUSE_BUCKET='<versioned-lakehouse-bucket>'
export GCP_SOURCE_BUCKET='<private-source-bucket>'
export OMEGA_GCP_ENVIRONMENT='staging'
export OMEGA_GHCR_PULL_SECRET_VERSION='<numeric-enabled-secret-version>'
```

Do not put the GHCR username or token in any of these variables.

## 1. Writer-fenced pre-deploy backup

First verify externally that GCP is the only writer, DNS still points to GCP,
AWS is fenced, and exactly one scheduler is running. Then run:

```bash
CONFIRM_GCP_BACKUP=1 make backup-gcp-canonical
```

The operation takes a bounded write outage while it:

1. takes the exclusive day-2 lock;
2. records the services that were running;
3. stops the scheduler and every known application writer;
4. creates compressed logical dumps of operational PostgreSQL and Gold;
5. verifies GCS versioning;
6. lists every live object twice under the same fence and requires identical
   key, size, generation, and checksum streams;
7. uploads dumps, object manifest, and a secret-free runtime image manifest
   with `ifGenerationMatch=0`;
8. uploads the immutable manifest last; and
9. restores exactly the previously-running services and requires one scheduler.

Record the emitted `manifest_uri`, `manifest_sha256`, and
`manifest_generation`. No `.env` or plaintext runtime/GHCR credential is
copied into the backup artifacts or evidence. Database dumps are nonetheless
sensitive production data (and can contain application-managed ciphertext), so
retain their private GCS access boundary. The object rollback point is the
exact set of recorded GCS generations; the script does not duplicate hundreds
of gigabytes under a second prefix.

## 2. Release gate and deploy

Do not run this until Release Images has succeeded 15/15 and the private pull
preflight has been demonstrated. Set only public identifiers:

```bash
export GCP_RELEASE_TAG='v1.45.207-beta'
export GCP_DEPLOY_REF='<full-approved-main-sha>'
export GCP_BACKUP_MANIFEST_URI='gs://.../_omega_backups/.../manifest.json'
export GCP_BACKUP_MANIFEST_SHA256='<sha256-from-step-1>'
CONFIRM_GCP_DEPLOY=1 make deploy-gcp-canonical
```

The remote operation verifies the artifact and backup before it pulls all 15
images through the server-owned auth helper. Only then does it fence writers,
recreate `postgres` and `postgres_gold` from the candidate Compose definition,
and prove that both existing named data volumes survived. Recreating the DB
containers is required so their `/docker-entrypoint-initdb.d` bind mounts move
from the old release to the candidate; `down`, `down -v`, and volume deletion
are never used.

Pending migrations run only through `scripts/apply_db_migrations.sh`, with the
shared production env and stable Compose project supplied explicitly. The
scheduler remains stopped while all non-scheduler services start. Promotion
requires:

- `/healthz` HTTP 200 and exact `VERSION`;
- `/readyz` HTTP 200;
- `/readyz?require_data=1` HTTP 200 with an affirmative payload;
- all 15 proprietary services configured by exact `@sha256` RepoDigest; and
- exactly one running Airflow scheduler.

Evidence is written below `docs/release-evidence/deploy-gcp/`; credential-like
values are redacted and the GHCR helper's pull output is never copied.

## 3. Isolated restore rehearsal

Use the same manifest values:

```bash
GCP_OBJECT_VERIFY_MODE=all make restore-rehearsal-gcp
```

`all` is mandatory for formal release closure. It verifies metadata, size, and
available checksum for every exact object generation. It restores both logical
dumps into newly-created, non-networked, ephemeral Docker volumes, checks the
operational and Gold migration ledgers, and removes the rehearsal containers
and volumes. The live Compose project and live volumes are not opened or
modified.

Because `pg_dumpall --clean` emits `DROP ROLE` and `CREATE ROLE` for its own
bootstrap `postgres` session user, the rehearsal validates and omits exactly
those two byte-exact statements inside the global role sections. It preserves
`ALTER ROLE postgres`, quoted or similarly named roles, and identical text in
database payloads. Any dump-format drift or any other SQL error remains fatal
under `psql -v ON_ERROR_STOP=1`.

`GCP_OBJECT_VERIFY_MODE=sample` exists only for fast diagnostics and is not
release evidence.

## Rollback boundary

There is intentionally no blind N-1 application rollback command. A release
may add schema or data that an old image cannot interpret. Before `current` is
promoted, a failed deploy keeps writers fenced and preserves the old `current`
link plus the verified pre-deploy snapshot. After promotion, rollback means a
coordinated restore to that snapshot (and therefore loss/reconciliation of
post-deploy writes) or a forward fix. It requires a separate irreversible
operation approval; never point old images at a newer schema by hand.

For an in-flight failure:

1. preserve the fail-closed fence;
2. save redacted evidence and container logs;
3. run the isolated restore rehearsal against the pre-deploy manifest;
4. decide forward-fix versus approved snapshot restore; and
5. keep AWS fenced throughout—AWS is DR/standby, not a second writer.
