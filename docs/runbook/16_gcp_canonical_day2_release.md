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
- `/opt/modecissions/shared/runtime-state` is one atomic symlink to a state
  bundle containing both `bootstrap-state.json` and
  `runtime-provenance.json`. Docker's `ExecStartPre` validates that pair, the
  current release identity, provenance checksum, and reboot-helper hashes. A
  partial backup/bootstrap/deploy therefore cannot restart Docker after a
  reboot or `SIGKILL`.

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
export GCP_CURRENT_LIVE_REF='<exact-40-char-ref-running-before-backup>'
export GCP_DEPLOY_REF='<exact-approved-origin-main-candidate>'
export OMEGA_GCP_ENVIRONMENT='staging'
export OMEGA_GHCR_PULL_SECRET_VERSION='<numeric-enabled-secret-version>'
export OMEGA_TERRAFORM_BIN='tofu'
export GCP_TERRAFORM_VAR_FILE='<absolute-path-to-reviewed-production.tfvars>'
export GCP_PROJECT_NUMBER='894064513501'
export GCP_BILLING_ACCOUNT_ID='01A3E0-B708F4-6EA299'
export GCP_PUBLIC_CONSOLE_DOMAIN='<exact-live-gcp-console-domain>'
export GCP_PUBLIC_WORKSPACE_DOMAIN='<exact-live-gcp-workspace-domain>'
export GCP_EXPECTED_OLD_STARTUP_SHA256='<fresh-readback-sha256>'
export GCP_GHCR_ACCESS_PLAN='/tmp/omega-gcp-ghcr-access.tfplan'
```

Do not put the GHCR username or token in any of these variables.

## 1. Reconcile startup metadata before legacy adoption

Do not run backup against the historical startup script. At the 5.5
reconciliation point, live evidence showed all of the following drift:

- Terraform state `source_sha` was
  `295ad0fd344c8a5f45afae960e974d481ee08973`;
- live GCE startup metadata embedded
  `6b12883c5b5ea0537120279ccbee4947137998a2`; a byte-exact JSON-value
  readback on 2026-08-11 had SHA-256
  `345fd1b9004b2fb4fed9eb459e536cc46c75053b5fa587d8f3d9837a2034242a`
  and fingerprint `hQkaMrta_Vc=` (always re-read both immediately before use);
- the live boot disk was 60 GB while state/default input was 30 GB;
- the billing budget was EUR 100 while the code default is USD;
- the data disk was 150 GB and machine type was `e2-standard-4`; and
- the existing managed-certificate domains were the live GCP console and
  workspace domains.

These values are evidence, not permission to apply. A real full read-only plan
with the live values proved that `metadata_startup_script` is ForceNew: it
planned `delete/create` of `google_compute_instance.app`. The same plan also
contained unrelated LB/IAM/secret drift. Never apply that full plan. Terraform
therefore keeps lifecycle ignore on this ForceNew attribute; the canonical
controller uses Compute `setMetadata` in place with the just-read metadata
fingerprint as a compare-and-swap.

Never plan with defaults or an example tfvars. First publish both immutable
source objects:

```bash
make prepare-gcp-artifacts
```

Create a reviewed production tfvars outside the repository. It must explicitly
set every value checked by `scripts/gcp_release.py`, including project/billing
identity, `boot_disk_size_gb = 60`, `data_disk_size_gb = 150`,
`monthly_budget_currency = "EUR"`, both exact live certificate domains,
`enable_https = true`, `enable_airflow_scheduler = true`, and the exact source
triple:

```hcl
source_bucket = "<GCP_SOURCE_BUCKET>"
source_object = "deploy-artifacts/<GCP_CURRENT_LIVE_REF>/repo.tar.gz"
source_sha    = "<GCP_CURRENT_LIVE_REF>"
```

Before adoption, obtain the startup script through
`instances describe --format=json(metadata)` and hash the decoded JSON string
bytes (not a shell-escaped representation). Set that exact value as
`GCP_EXPECTED_OLD_STARTUP_SHA256`, then run:

```bash
export GCP_STARTUP_SOURCE_REF="$GCP_CURRENT_LIVE_REF"
CONFIRM_GCP_STARTUP_ADOPTION=1 make adopt-gcp-startup-metadata
```

Before the CAS, this command saves the exact prior script under an immutable
GCS generation and writes its SHA-256, live fingerprint, instance id/start
timestamp, and a complete restore contract to secret-free local evidence. It
then preserves every other metadata key, uses the backed-up fingerprint in the
`setMetadata` request, reads back the exact new hash/fingerprint, and proves
the VM id, `RUNNING` status, and last-start timestamp did not change. A stale
old hash or fingerprint fails closed. The evidence is already sufficient to
run the inverse CAS before the forward mutation is attempted:

```bash
export GCP_STARTUP_BACKUP_URI='gs://.../startup-metadata-backups/.../....sh'
export GCP_STARTUP_BACKUP_GENERATION='<exact-generation>'
export GCP_STARTUP_BACKUP_SHA256='<prior-script-sha256>'
export GCP_EXPECTED_CURRENT_STARTUP_SHA256='<forward-script-sha256>'
CONFIRM_GCP_STARTUP_RESTORE=1 make restore-gcp-startup-metadata
```

The backup/deploy controllers validate the explicit release-critical tfvars,
render the startup script directly from that reviewed file, and compare its
hash to live metadata. They do not claim the known full-stack Terraform drift
is clean. `OMEGA_TERRAFORM_BIN` defaults to `tofu` and fails closed if the
configured binary is unavailable.

### Private GHCR Secret Manager resources

Do not apply the full Terraform graph. Create one saved, target-only plan and
require its JSON actions to be exactly two creates: the credential secret
container and its resource-scoped `roles/secretmanager.secretAccessor` member.
Zero updates, deletes, replacements, replacement paths, or output drift are
allowed.

```bash
make plan-gcp-ghcr-access
tofu -chdir=infra/terraform-gcp show "$GCP_GHCR_ACCESS_PLAN"
CONFIRM_GCP_GHCR_ACCESS_APPLY=1 make apply-gcp-ghcr-access
```

The apply command consumes only that already-validated saved plan; it never
reconstructs a direct `-target` apply. It then reads back the secret container
and the exact VM service-account member on the resource policy. Add the JSON
credential version separately through the approved secret-entry path; neither
username nor token belongs in Terraform state, arguments, logs, or evidence.

## 2. Authenticated rollback-image preflight

Before tag creation, prove the approved published rollback tag is pullable
15/15 through the VM-owned Secret Manager credential:

```bash
export GCP_IMAGE_PREFLIGHT_PURPOSE='rollback'
export GCP_IMAGE_PREFLIGHT_TAG='<approved-published-rollback-tag>'
export GCP_IMAGE_PREFLIGHT_REF='<exact-rollback-tag-commit>'
CONFIRM_GCP_IMAGE_PREFLIGHT=1 make gcp-image-preflight
```

This downloads an exact candidate helper artifact, uses an ephemeral
`DOCKER_CONFIG`, and stores only a secret-free immutable digest lock under
`/opt/modecissions/shared/image-preflights/`. A result below 15/15 is a hard
stop. The same target with `PURPOSE=release` is mandatory after Release Images
publishes the new tag and before deploy.

## 3. Writer-fenced pre-deploy backup

First verify externally that GCP is the only writer, DNS still points to GCP,
AWS is fenced, and exactly one scheduler is running. Then run:

```bash
CONFIRM_GCP_BACKUP=1 make backup-gcp-canonical
```

On a legacy runtime with no canonical state, the backup first obtains the
exact pre-tag candidate artifact, stages ref-addressed reboot helpers in
`shared/bin`, installs the Docker operation gate, and generates provenance in
a temporary state bundle. It validates Compose, exact live VERSION,
`healthz`, 15 healthy proprietary services, exactly one scheduler, and all
one-shot exit states against that temporary record. Only then is the atomic
`runtime-state` link published. A post-record health failure leaves the prior
state unchanged.

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

## 4. Release gate and deploy

Do not run this until Release Images has succeeded 15/15 and the private pull
preflight has been demonstrated. Set only public identifiers:

```bash
export GCP_RELEASE_TAG='v1.45.207-beta'
export GCP_DEPLOY_REF='<full-approved-main-sha>'
export GCP_BACKUP_MANIFEST_URI='gs://.../_omega_backups/.../manifest.json'
export GCP_BACKUP_MANIFEST_SHA256='<sha256-from-step-3>'
CONFIRM_GCP_DEPLOY=1 make deploy-gcp-canonical
```

Before this command, update the same reviewed tfvars source triple to the final
tag commit at `deploy-artifacts/<GCP_DEPLOY_REF>/repo.tar.gz`, set
`GCP_STARTUP_SOURCE_REF=$GCP_DEPLOY_REF`, re-read the then-live startup hash,
and run the same reversible metadata CAS adoption. Do not apply the full
Terraform drift. The deploy controller repeats the explicit release-critical
tfvars validation, renders the expected startup bytes itself, and performs the
live metadata read-back. It will not accept an operator-supplied new hash.

The remote operation verifies the artifact and backup before it pulls all 15
images through the server-owned auth helper. Only then does it fence writers,
recreate `postgres` and `postgres_gold` from the candidate Compose definition,
and prove that both existing named data volumes survived. Recreating the DB
containers is required so their `/docker-entrypoint-initdb.d` bind mounts move
from the old release to the candidate; `down`, `down -v`, and volume deletion
are never used.

Pending migrations run only through `scripts/apply_db_migrations.sh`, with the
shared production env, stable Compose project, exact old/candidate refs,
release version, and checksum-bound baseline/release migration manifests
supplied explicitly. Bootstrap mode is forbidden for day-2. The
scheduler remains stopped while all non-scheduler services start. Promotion
requires:

- `/healthz` HTTP 200 and exact `VERSION`;
- `/readyz` HTTP 200;
- `/readyz?require_data=1` HTTP 200 with an affirmative payload;
- all 15 proprietary services configured by exact `@sha256` RepoDigest; and
- exactly one running Airflow scheduler.

Evidence is written below `docs/release-evidence/deploy-gcp/`; credential-like
values are redacted and the GHCR helper's pull output is never copied.

## 5. Isolated restore rehearsal

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

The dump is never filtered or rewritten. Each rehearsal database starts with a
fresh, collision-checked `omega_rehearsal_*` superuser distinct from every role
in the unmodified `pg_dumpall` stream. Restore runs as that role under
`psql -v ON_ERROR_STOP=1`. Database images are fixed by both the backup's exact
RepoDigest and local ImageID, and `--pull never` forbids substitution.

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
