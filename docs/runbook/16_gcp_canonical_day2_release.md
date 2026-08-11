# GCP-canonical day-2 release

This is the only supported post-bootstrap release path for the single-node GCP
production environment. It deploys an already-published annotated tag; it does
not build images, move tags, change DNS, create another scheduler, promote AWS,
or read a GHCR credential on the operator machine.

> **Checkpoint 5.5 is currently BLOCKED. Do not execute sections 2–5.** The
> verified routing/scheduler evidence says that the destination AWS ALB still
> serves the application and does not prove a database/API hard fence. The
> accepted `omega.gcp-canonical-routing-scheduler-attestation/v1` schema is
> deliberately observation-only and can authorize neither backup,
> reconciliation, nor deploy. A self-asserted boolean `PASS` is not an
> acceptable replacement. A separate, independently verifiable zero-AWS-writer
> evidence design and explicit operator approval of the reordered release
> window described below are required first.

## Safety model

- `origin/main`, the annotated tag, `VERSION`, and the full commit SHA must all
  identify the same release.
- The source archive is uploaded to
  `gs://$GCP_SOURCE_BUCKET/deploy-artifacts/<sha>/repo.tar.gz` with
  `ifGenerationMatch=0` and commit/checksum metadata. An existing object is
  reusable only when URI, generation, byte size, and SHA-256 all match. Every
  remote download requests that exact generation and verifies the returned
  bytes before extraction.
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
  current release identity, provenance checksum, source artifact byte identity,
  and the reboot/runtime/safe-I/O helper hashes. A
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
export GCP_APP_INSTANCE_ID='<immutable-numeric-instance-id>'
export GCP_REGION='<region>'
export GCP_LAKEHOUSE_BUCKET='<versioned-lakehouse-bucket>'
export GCP_SOURCE_BUCKET='<private-source-bucket>'
export GCP_CURRENT_LIVE_REF='<exact-40-char-ref-running-before-backup>'
export GCP_DEPLOY_REF='<exact-approved-origin-main-candidate>'
export GCP_RELEASE_TAG='<exact-annotated-published-release-tag>'
export OMEGA_GCP_ENVIRONMENT='staging'
export OMEGA_GHCR_PULL_SECRET_VERSION='<numeric-enabled-secret-version>'
export OMEGA_TERRAFORM_BIN='tofu'
export GCP_TERRAFORM_VAR_FILE='<absolute-path-to-reviewed-production.tfvars>'
export GCP_PROJECT_NUMBER='894064513501'
export GCP_BILLING_ACCOUNT_ID='01A3E0-B708F4-6EA299'
export GCP_PUBLIC_CONSOLE_DOMAIN='console.7businesssolutions.com'
export GCP_PUBLIC_WORKSPACE_DOMAIN='workspace.7businesssolutions.com'
export GCP_EXPECTED_OLD_STARTUP_SHA256='<fresh-readback-sha256>'
export GCP_SOURCE_GENERATION='<exact-generation-from-prepare-artifacts>'
export GCP_SOURCE_SIZE_BYTES='<exact-byte-size-from-prepare-artifacts>'
export GCP_SOURCE_ARCHIVE_SHA256='<exact-sha256-from-prepare-artifacts>'
export GCP_RELEASE_BACKUP_BUCKET='<private-dedicated-release-backup-bucket>'
export GCP_EXTERNAL_ROUTING_SCHEDULER_ATTESTATION='<external-mode-0600-pre-operation-evidence.json>'
export GCP_POSTDEPLOY_EXTERNAL_ROUTING_SCHEDULER_ATTESTATION='<distinct-external-mode-0600-postdeploy-evidence.json>'
GCP_PRIVATE_PLAN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/omega-gcp-plan.XXXXXX")"
chmod 700 "$GCP_PRIVATE_PLAN_DIR"
export GCP_GHCR_ACCESS_PLAN="$GCP_PRIVATE_PLAN_DIR/grants.tfplan"
```

Do not put the GHCR username or token in any of these variables.

The accepted observation-only attestation is exact-allowlisted to:

- origin ALB `modecissions-public-255609366.us-east-1.elb.amazonaws.com`,
  frozen source `ee35b035044cffea7270160d829cb17505c4d16a`, `/healthz=502`;
- destination ALB
  `modecissions-public-1973504078.us-east-1.elb.amazonaws.com`,
  `/healthz=200`, `/airflow/health=200`, scheduler `unhealthy`, heartbeat
  `2026-08-07T01:26:04.241563+00:00`; and
- forensic reference
  `codex-session-event:019ff064-b6ab-7ac1-aa60-078ec27f2c29@2026-08-11T10:47:44.485Z#payload.message+LF`,
  SHA-256
  `adf099625e43bbe93ea66151f132a5baa9e6d5617e0b037e5005b604429238a2`.

Every HTTPS probe resolves and visits every A record but uses
`console.7businesssolutions.com` for both verified TLS SNI and HTTP `Host`;
default CA/hostname verification is mandatory. Canonical DNS is checked
separately and must resolve only to GCP. These observations prove routing and
scheduler state, not that AWS database/API writes are impossible.

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

Never plan with defaults or an example tfvars. First publish the one immutable
candidate/helper source object. The historical live release is reconciled from
its existing `/opt/modecissions/releases/<sha>` tree and runtime provenance; the
controller does not require or try to create a missing legacy source archive:

```bash
make prepare-gcp-artifacts
```

Create a reviewed production tfvars outside the repository. It must explicitly
set every value checked by `scripts/gcp_release.py`, including project/billing
identity, `boot_disk_size_gb = 60`, `data_disk_size_gb = 150`,
`monthly_budget_currency = "EUR"`, both exact live certificate domains,
`enable_https = true`, `enable_airflow_scheduler = true`, and the exact source
artifact identity:

```hcl
source_bucket        = "<GCP_SOURCE_BUCKET>"
source_object        = "deploy-artifacts/<GCP_DEPLOY_REF>/repo.tar.gz"
source_sha           = "<GCP_DEPLOY_REF>"
source_generation    = "<exact-generation>"
source_size_bytes    = <exact-positive-size>
source_archive_sha256 = "<exact-sha256>"
```

Before adoption, obtain the startup script through
`instances describe --format=json(metadata)` and hash the decoded JSON string
bytes (not a shell-escaped representation). Set that exact value as
`GCP_EXPECTED_OLD_STARTUP_SHA256`, then run:

```bash
export GCP_STARTUP_SOURCE_REF="$GCP_DEPLOY_REF"
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

The adoption controller first uses the exact helper artifact to publish and
validate the atomic runtime-state bundle while the historical startup script
is still installed. Only after that runtime adoption succeeds does it read and
back up the exact old startup bytes, publish the restore contract, and perform
the fingerprint-bound metadata CAS. The backup command itself never performs
legacy adoption and fails closed until this step is complete.

The backup/deploy controllers validate the explicit release-critical tfvars,
render the startup script directly from that reviewed file, and compare its
hash to live metadata. They do not claim the known full-stack Terraform drift
is clean. `OMEGA_TERRAFORM_BIN` defaults to `tofu` and fails closed if the
configured binary is unavailable.

### Private GHCR Secret Manager resources

Do not apply the full Terraform graph. IAM is a two-stage, no-gap transition.
Stage A permits exactly seven creates: the GHCR credential secret container,
its resource-scoped accessor member, and resource-scoped accessor members for
the five host secrets the bootstrap actually reads. Zero updates, deletes,
replacements, replacement paths, or output drift are allowed in this stage.
The plan must live outside the repository under an operator-owned mode-0700
directory; the controller makes the plan mode 0600, fsyncs it, and emits its
SHA-256.

```bash
export GCP_GHCR_IAM_STAGE=grants
make plan-gcp-ghcr-access
tofu -chdir=infra/terraform-gcp show "$GCP_GHCR_ACCESS_PLAN"
export GCP_GHCR_ACCESS_PLAN_SHA256='<exact-sha256-emitted-by-plan>'
CONFIRM_GCP_GHCR_ACCESS_APPLY=1 make apply-gcp-ghcr-access
```

The apply command consumes only that already-validated saved plan; it never
reconstructs a direct `-target` apply. It then reads back the secret container
and all six exact VM service-account members on their resource policies. Add
the JSON credential version separately through the approved secret-entry path;
neither username nor token belongs in Terraform state, arguments, logs, or
evidence.

Only after those six grants are both read back and effective, use a different
new saved plan to revoke the historical project-wide Secret Manager accessor:

```bash
export GCP_GHCR_IAM_STAGE=revoke
export GCP_GHCR_ACCESS_PLAN="$GCP_PRIVATE_PLAN_DIR/revoke.tfplan"
make plan-gcp-ghcr-access
tofu -chdir=infra/terraform-gcp show "$GCP_GHCR_ACCESS_PLAN"
export GCP_GHCR_ACCESS_PLAN_SHA256='<exact-sha256-emitted-by-revoke-plan>'
CONFIRM_GCP_GHCR_ACCESS_APPLY=1 make apply-gcp-ghcr-access
```

The revoke stage permits exactly one delete and no other drift. Post-apply
verification proves all six allowlisted permissions still work and a
non-allowlisted canary is denied, without reading any secret payload.

### Dedicated release-backup storage

Provision the release-backup bucket through its separate saved-plan gate before
the first canonical backup. Never apply the full Terraform graph. The plan is
valid only when it contains exactly these three creates and no update, delete,
replacement, replacement path, or unrelated output drift:

- `google_storage_bucket.release_backups`;
- `google_project_iam_custom_role.release_backup_writer`; and
- `google_storage_bucket_iam_member.app_release_backup`.

Use a new mode-0700 directory outside the repository and review both the
machine-readable contract emitted by the controller and the human-readable
saved plan before authorizing apply:

```bash
GCP_BACKUP_PLAN_DIR="$(mktemp -d "${TMPDIR:-/tmp}/omega-gcp-backup-plan.XXXXXX")"
chmod 700 "$GCP_BACKUP_PLAN_DIR"
export GCP_BACKUP_STORAGE_PLAN="$GCP_BACKUP_PLAN_DIR/release-backup.tfplan"
make plan-gcp-backup-storage
tofu -chdir=infra/terraform-gcp show "$GCP_BACKUP_STORAGE_PLAN"
export GCP_BACKUP_STORAGE_PLAN_SHA256='<exact-sha256-emitted-by-plan>'
CONFIRM_GCP_BACKUP_STORAGE_APPLY=1 make apply-gcp-backup-storage
```

Apply consumes only that exact saved plan and SHA-256. Read-back must prove the
private bucket controls, versioning, soft-delete and retention settings, exact
three-permission custom role, resource-scoped VM binding, and unchanged live
canonical instance identity.

## 2. Image authority and preflights — BLOCKED

The commands in this section are reference contracts, not current execution
instructions. Do not run them until the zero-AWS-writer blocker and release
ordering decision are closed. A tag plus OCI labels is not image authority.
For 1.45.207, the annotated tag must bind
`OMEGA-Release-Candidate-Manifest-SHA256`; the server-owned GCP pull path must
fetch that private sealed manifest by digest and require its exact 15 service
digests. For legacy rollback tags without that annotation, the verified
backup/runtime `runtime-images.json` is the authority and every image is pulled
by its recorded digest. Labels are only a secondary consistency check.

Before either image preflight, take a full writer-fenced rollback baseline from
the live runtime. This phase requires the intended 1.45.207 tag to remain
absent. It binds the helper to exact current `origin/main`, but records the live
runtime's source ref/version independently; it does not create the one-time
pre-deploy attestation used by reconciliation:

```bash
export GCP_BACKUP_PHASE='rollback-baseline'
export GCP_RELEASE_TAG='v1.45.207-beta' # must still be absent
export GCP_CURRENT_LIVE_REF='<exact-live-runtime-source-ref>'
export GCP_CURRENT_LIVE_VERSION='1.45.205-beta'
# BLOCKED: reference only; zero-AWS-writer evidence is not yet sufficient.
# CONFIRM_GCP_BACKUP=1 make backup-gcp-canonical
```

Record all eight immutable authority values emitted by that operation:

- `runtime_images_uri`, `runtime_images_generation`,
  `runtime_images_size_bytes`, `runtime_images_sha256`;
- `manifest_uri`, `manifest_generation`, `manifest_size_bytes`,
  `manifest_sha256`.

Then prove the approved legacy rollback release is pullable 15/15 through the
VM-owned Secret Manager credential. `GCP_IMAGE_PREFLIGHT_REF` is the exact
`source_ref` emitted by the rollback-baseline backup, not the commit peeled
from the legacy tag. The controller verifies the annotated legacy tag and its
VERSION separately and records both identities, but only the checksum-bound
backup RepoDigests and ImageIDs authorize image pulls:

```bash
export GCP_IMAGE_PREFLIGHT_PURPOSE='rollback'
export GCP_IMAGE_PREFLIGHT_TAG='<approved-published-rollback-tag>'
export GCP_IMAGE_PREFLIGHT_REF='<exact-live-runtime-source-ref-from-rollback-baseline>'
export GCP_ROLLBACK_RUNTIME_IMAGES_URI='<runtime_images_uri>'
export GCP_ROLLBACK_RUNTIME_IMAGES_GENERATION='<runtime_images_generation>'
export GCP_ROLLBACK_RUNTIME_IMAGES_SIZE_BYTES='<runtime_images_size_bytes>'
export GCP_ROLLBACK_RUNTIME_IMAGES_SHA256='<runtime_images_sha256>'
export GCP_ROLLBACK_BACKUP_MANIFEST_URI='<manifest_uri>'
export GCP_ROLLBACK_BACKUP_MANIFEST_GENERATION='<manifest_generation>'
export GCP_ROLLBACK_BACKUP_MANIFEST_SIZE_BYTES='<manifest_size_bytes>'
export GCP_ROLLBACK_BACKUP_MANIFEST_SHA256='<manifest_sha256>'
# BLOCKED: reference only; do not invoke gcp-image-preflight yet.
```

This downloads an exact candidate helper artifact, uses an ephemeral
`DOCKER_CONFIG`, and stores only a secret-free immutable digest lock under
`/opt/modecissions/shared/image-preflights/`. A result below 15/15 is a hard
stop.

Before tag creation, the manual release-candidate workflow must also have built
and sealed all 15 images for the exact approved `origin/main` commit. Prove that
exact candidate set through the same server-owned credential:

```bash
export GCP_IMAGE_PREFLIGHT_PURPOSE='release'
export GCP_IMAGE_PREFLIGHT_TAG="candidate-${GCP_DEPLOY_REF}"
export GCP_IMAGE_PREFLIGHT_REF="$GCP_DEPLOY_REF"
export GCP_RELEASE_CANDIDATE_WORKFLOW_RUN_ID='<exact-successful-run-id>'
export GCP_RELEASE_CANDIDATE_WORKFLOW_RUN_ATTEMPT='<exact-run-attempt>'
# BLOCKED: reference only; do not invoke the candidate preflight yet.
```

The controller authenticates with `gh`, verifies that exact run is completed
and successful for the exact main SHA and workflow path, downloads the unique
`release-candidate-<SHA>` artifact, and binds the VM proof to its exact
canonical payload bytes. No GitHub credential is sent to the VM.

Only these two 15/15 pre-tag proofs permit creation of the annotated immutable
release tag. After the tag-triggered Release Images workflow promotes the
sealed candidate digests without rebuilding, repeat the release proof against
the published tag before deploy:

```bash
export GCP_IMAGE_PREFLIGHT_PURPOSE='release'
export GCP_IMAGE_PREFLIGHT_TAG="$GCP_RELEASE_TAG"
export GCP_IMAGE_PREFLIGHT_REF="$GCP_DEPLOY_REF"
# BLOCKED: requires the authorized reordered window and sealed-manifest binding.
```

## 3. Fresh writer-fenced pre-deploy backup — BLOCKED

First prove externally that GCP is the only writer, DNS still points to GCP,
AWS database and API writes are independently hard-fenced, and exactly one
scheduler is running. The current scoped routing/scheduler evidence explicitly
does not prove that hard fence, so the controller stops before upload or remote
mutation. The safe reordered proposal requires this to be a **second, fresh
backup after the annotated tag, Release Images 15/15, and published 15/15 pull
receipt**, not the earlier pre-tag snapshot. The backup command also requires
the exact published release tag.

```bash
# BLOCKED: do not invoke backup-gcp-canonical in the current state.
```

Set `GCP_BACKUP_PHASE=predeploy` for this second backup; the controller now
requires the published annotated release tag and its sealed-manifest binding.
Only this second backup's four manifest values may feed reconciliation and
deploy. The earlier rollback-baseline manifest remains immutable rollback
evidence and must not be substituted.

The future backup requires the startup/runtime adoption in step 1 to have completed.
It never creates canonical runtime state as a side effect. It revalidates the
already-published atomic state, exact live VERSION, `healthz`, 15 healthy
proprietary services, exactly one scheduler, and all one-shot exit states before
it begins the writer fence.

The operation takes a bounded write outage while it:

1. takes the exclusive day-2 lock;
2. records the services that were running;
3. stops the scheduler and every known application writer;
4. creates compressed logical dumps of operational PostgreSQL and Gold;
5. verifies GCS versioning;
6. lists every live object twice under the same fence and requires identical
   key, size, generation, and checksum streams;
7. applies a GCS `temporaryHold` to every exact recorded live generation with
   generation/metageneration preconditions, then reads every hold back;
8. uploads dumps, the held-generation object manifest, and a secret-free
   runtime image manifest with `ifGenerationMatch=0`;
9. re-verifies every hold immediately before uploading the immutable manifest
   last; and
10. restores exactly the previously-running services and requires one scheduler.

The published preflight stores an immutable `completion.json` receipt binding
its completion timestamp plus the manifest and 15-image lock SHA-256 values.
Reconciliation rejects a backup whose manifest completion is not strictly
later than that receipt, or whose final server-owned attestation is older than
30 minutes. Writes accepted between snapshot completion and the later writer
fence are the explicit recovery-point objective (RPO). Do not proceed unless a
delta reconciliation proves it is zero or the operator explicitly approves
that bounded RPO with `CONFIRM_GCP_SNAPSHOT_TO_FENCE_RPO=1`.

Record the emitted `manifest_uri`, `manifest_sha256`, and
`manifest_generation`, plus `manifest_size_bytes`. No `.env` or plaintext
runtime/GHCR credential is copied into the backup artifacts or evidence.
Database dumps are nonetheless sensitive production data (and can contain
application-managed ciphertext), so retain their private GCS access boundary.
The object rollback point is the exact set of recorded GCS generations; the
script does not duplicate hundreds of gigabytes under a second prefix.
Every recorded generation remains protected by its `temporaryHold` after the
backup and deploy complete. There is no implicit hold release or object
deletion in the backup, deploy, rehearsal, or cleanup paths. Releasing those
holds requires a separate, explicit, reviewed, and approved retention action
after the rollback window has been closed.

The manifest also checksum-binds each database's exact pre-fence connection
limit, database-level `default_transaction_read_only` setting (including
absence), and effective read-only value. `pg_dumpall` captures the temporary
connection-limit/read-only fence by design. Restore uses the unmodified dumps,
keeps the restored databases fenced through every verification gate, and
reapplies the recorded pre-fence policy only at an explicit cutover.

## 4. Atomic stale pipeline-run reconciliation — BLOCKED

The originally requested order (backup → reconciliation → tag → Release
Images) is unsafe for the current live 1.45.205 runtime. Several ordinary live
writers can reopen these deterministic run ids; therefore the old runtime must
not restart after the CAS. Holding that fence while tag CI runs can exceed the
handoff and cause an extended production outage. The only reviewed safe
proposal is:

1. independently prove zero AWS writers and obtain explicit authorization for
   this reordered maintenance window;
2. create the exact annotated tag and complete Release Images 15/15;
3. perform the published, sealed-manifest-bound 15/15 GCP pull;
4. take the second fresh backup from section 3;
5. approve or reconcile the snapshot-to-fence RPO;
6. reconcile the exact 17 rows, keeping the GCP writer fence continuously; and
7. hand off immediately to day-2 deploy within 300–3600 seconds.

Do not execute this proposal without the required user decision. The current
observation-only AWS evidence causes both controller and remote helper to stop
before marker creation, service stop, or CAS.

Prepare the independently reviewed JSON manifest outside the repository. It
uses schema `omega.pipeline-run-reconciliation/v1`, one unique `change_id`, and
an exact `runs` array. Each row identifies `run_id`, canonical tenant/workspace
UUIDs, exact `running` status, exact `started_at`, exact `fencing_token`, one
terminal target (`failed` or `blocked`), a bounded reason, and non-secret
forensic evidence including an explicit UTC `observed_at`. For the Checkpoint
5.5 closeout, require exactly the independently inventoried 17 rows:

```bash
export GCP_PIPELINE_RECONCILIATION_MANIFEST='<absolute-external-manifest.json>'
export GCP_PIPELINE_RECONCILIATION_EXPECTED_COUNT='17'
export GCP_RELEASE_TAG='<already-published-exact-annotated-tag>'
export GCP_RECONCILIATION_HANDOFF_TIMEOUT='1800'
# Requires a separate explicit operator decision if delta reconciliation is nonzero:
# export CONFIRM_GCP_SNAPSHOT_TO_FENCE_RPO=1
chmod 0600 "$GCP_PIPELINE_RECONCILIATION_MANIFEST"

export GCP_BACKUP_MANIFEST_URI='gs://.../_omega_backups/.../manifest.json'
export GCP_BACKUP_MANIFEST_GENERATION='<exact-generation-from-step-3>'
export GCP_BACKUP_MANIFEST_SIZE_BYTES='<exact-byte-size-from-step-3>'
export GCP_BACKUP_MANIFEST_SHA256='<sha256-from-step-3>'

# BLOCKED: do not invoke reconcile-gcp-pipeline-runs in the current state.
```

The controller rejects repository files, symlinks, hard links, non-owner files,
non-0600 modes, duplicate JSON keys, duplicate run identities, extra fields,
unbounded evidence, credential-shaped evidence, non-UTC timestamps, or a count
other than the explicit gate. It uploads the validated bytes with
`ifGenerationMatch=0`, reads back the exact GCS generation, byte size, and
SHA-256, and sends only that immutable identity to the VM.

The remote helper takes the exclusive day-2 lock and requires the exact fresh
server-owned pre-deploy attestation to still be `ready`, unconsumed, and bound
to the current and candidate refs plus the exact backup generation. In one
PostgreSQL transaction it locks all identified rows and compares the full
scope, `running` status, `started_at`, and fencing token. Every lease must be
expired or absent. Any missing, active, or changed row aborts the entire
transaction.

On success it preserves every row, writes the terminal state, clears lease and
heartbeat fields, increments the fencing token, and appends the immutable
manifest identity, reason, forensic evidence, prior state/token, and transition
time under `extra.reconciliation`. It then reads back exactly N
rows and the terminal distribution before commit. There is no `DELETE`, no
best-effort partial mode, and no hardcoded run inventory in the code. Preserve
the emitted evidence and keep the pre-deploy attestation unconsumed for deploy.
The incremented token plus terminal status and absent lease invalidate every
pre-reconciliation lease holder: the canonical materialization heartbeat and
finish paths require `status='running'`, the prior exact fencing token, and an
unexpired lease, so a stale scheduler worker cannot rewrite the reconciled
outcome.

## 5. Release gate and deploy — BLOCKED

Do not run this until the reordered sequence is explicitly authorized, Release
Images has succeeded 15/15, the sealed-manifest-bound published pull has been
demonstrated, the second backup is fresh, and reconciliation has handed over a
still-live writer fence. The pre-deploy external attestation must be fresh and
must use a future independently verifiable zero-writer schema; the current
routing/scheduler schema can only return `BLOCKED`.

```bash
export GCP_RELEASE_TAG='v1.45.207-beta'
export GCP_DEPLOY_REF='<full-approved-main-sha>'
export GCP_BACKUP_MANIFEST_URI='gs://.../_omega_backups/.../manifest.json'
export GCP_BACKUP_MANIFEST_GENERATION='<exact-generation-from-step-3>'
export GCP_BACKUP_MANIFEST_SIZE_BYTES='<exact-byte-size-from-step-3>'
export GCP_BACKUP_MANIFEST_SHA256='<sha256-from-step-3>'
export GCP_POSTDEPLOY_EXTERNAL_ROUTING_SCHEDULER_ATTESTATION='<distinct-evidence-produced-after-remote-deploy-completion>'
# BLOCKED: do not invoke deploy-gcp-canonical in the current state.
```

After a successful remote deploy, the controller first persists partial
evidence, then waits up to the explicit bounded timeout for a **distinct**
mode-0600 attestation whose SHA-256 differs from the pre-deploy bytes and whose
observation timestamp is strictly later than remote deploy completion. A stale,
reused, equal-boundary, or missing attestation fails the final gate. A remote
failure never waits for this evidence; its failure evidence is returned
immediately. Live DNS, all allowlisted AWS ALB A records, verified CA/SNI/Host,
`/healthz`, and `/airflow/health` are probed again after deploy.

Before this command, update the same reviewed tfvars source identity to the final
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

Evidence is written outside the repository under
`/var/tmp/omega-gcp-release-evidence/` by default (or the explicit private
`OMEGA_GCP_EVIDENCE_ROOT`). Files and directories are owner-only, values are
recursively redacted, and the GHCR helper's pull output is never copied.

## 6. Isolated restore rehearsal

Use the same manifest values:

```bash
GCP_OBJECT_VERIFY_MODE=all make restore-rehearsal-gcp
```

`all` is mandatory for formal release closure. It verifies metadata, size,
available checksum, exact metageneration, and the live `temporaryHold` for every
exact object generation. It restores both logical
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
