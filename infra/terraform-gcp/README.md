# OMEGA Google Cloud Terraform

Separate Google Cloud deployment stack for OMEGA. This does not modify or depend on the AWS Terraform tree.

Initial beta shape:

- Compute Engine single-node host
- Docker Compose for the existing OMEGA services
- Private VM, IAP SSH, no public SSH
- External HTTP Load Balancer for the console
- HTTPS Load Balancer using the pre-existing shared Certificate Manager map
- Cloud Armor rate limiting
- GCS lakehouse and source buckets
- Secret Manager placeholders
- Billing budget alerts in the billing account currency

This module is canonical-writer/HTTPS-only. The two legacy technical IPs,
forwarding rules, HTTP proxies, workspace URL map, and failed certificate stay
declaratively owned. The only allowed edge mutation retargets both legacy HTTP
proxies in place to the canonical HTTPS redirect map; their IPs/rules remain
stable. No edge resource is forgotten, deleted, released, or replaced.

Canonical HTTPS is fixed to `console.7businesssolutions.com` and
`workspace.7businesssolutions.com`. Terraform preserves the external
`sevenbs-production-map` attachment on the target proxy. The failed legacy
Compute SSL certificate remains quarantined and destroy-protected; cleanup is
a separate reviewed operation. Before any edge apply, run the read-only TLS
gate and require the exact forwarding IP, proxy/map association, canonical map
entries, certificate state, authoritative DNS, and both HTTPS probes to pass.

The canonical VM is never replaced by this foundation change. Terraform keeps
`metadata_startup_script` under `ignore_changes` because provider ownership of
that ForceNew field would replace the writer VM. There is nevertheless only
one startup writer: `scripts/gcp/terraform_transaction.py` invokes the sealed
`startup_metadata_transaction.py` internally, after OpenTofu succeeds, to do
one fingerprint CAS, one intent-protected reset, and one exact server-owned
foundation handoff. Never run the embedded helper directly. The explicit
operator entry points are `make gcp-foundation-plan`,
`make gcp-foundation-apply`, `make gcp-foundation-status`, and
`make gcp-foundation-recover` (the generic Terraform targets select the same
controller). It creates a fresh
refresh plan with fixed arguments, derives JSON from the same open plan
descriptor, validates exact actions/content/drift, and later applies that same
sealed inode. Manual plan JSON, caller-supplied hashes, `target`, `replace`,
destroy, and refresh-only paths are not accepted. Its mode-0400 release
authority is produced from the approved GitHub main/PR record plus exact GCS
object generation/size/hash and a reconstructed Git-tree identity for every
tar member, boot image, package versions, secret versions,
controller/predecessor and live startup predecessor; arbitrary tfvars are not
authority. The schema-v2 generator repeats its live VM, secret and GitHub
projections before returning. Run the official transaction with the explicit
operator account, GitHub CLI config and gcloud config. It executes the exact
edge gate immediately before plan, immediately before apply, and after apply.
Before the foundation profile, the separate `secret-adoption` profile imports
exactly the three pre-existing Control Room evidence secret containers; no
secret version or payload is imported. Apply progress is append-only and
crash-safe: intent, zero-exit result, indeterminate failure and final postcheck
receipt are distinct. For foundation, the final receipt additionally seals the
metadata intent/receipt and reset intent/server handoff receipt. TLS alone can
never produce `APPLIED_AND_POSTCHECKED`: the controller repeats the live VM and
current-boot server receipt read-back after TLS. Recovery may resume only a postcheck,
never reruns an indeterminate saved plan, and never issues a second reset after
a durable reset intent.

The legacy project-wide Secret Manager accessor grant is removed only through
`scripts/gcp/iam_revoke_transaction.py`. Its active locked outer controller
binds the sole-delete inner plan, proves six resource grants and 64 forbidden
resources, and restores and verifies the broad grant after any failed apply,
postcheck, signal or interrupted intent. Direct generic IAM apply is rejected.

OMEGA uses the cloud-neutral `LakehouseStorage` provider with native `gs://`
URIs on GCP. DuckDB refinement can additionally use GCS HMAC credentials from
Secret Manager when it needs direct Parquet reads.

The effective GCP Compose combines `infra/docker-compose.yml` with the
generated `infra/docker-compose.gcp.yml`. MCP Infra defaults to two concurrent
PDF workers and container limits of `1536m` memory, `2.0` CPUs, and `128` PIDs.
Operators can override `MCP_INFRA_PDF_MAX_WORKERS` (range `1` to `4`),
`MCP_INFRA_MEM_LIMIT`, `MCP_INFRA_CPUS`, and `MCP_INFRA_PIDS_LIMIT` in
`infra/.env`.

GCS HMAC lakehouse credentials are read from Secret Manager secrets
`omega-<env>-gcs_hmac_access_key_id` and
`omega-<env>-gcs_hmac_secret_access_key`. This stack creates the secret
containers but does not create HMAC keys, because some GCP organizations block
service-account key creation with `iam.disableServiceAccountKeyCreation`.

Private GHCR pulls use the same server-owned boundary. Terraform creates only
the `omega-<env>-ghcr_pull_credentials` container and grants the VM service
account access on that secret resource; it never creates a secret version or
stores a credential in Terraform state. An operator must add a JSON version
with `username` and `token` outside Terraform.

`release/ghcr-auth-run.sh` accepts only an explicit numeric Secret Manager
version, obtains the VM identity from the metadata server, and keeps Docker
authentication in a temporary host-only `DOCKER_CONFIG`. Run
`release/preflight-release-images.sh` through that wrapper before a release or
rollback. The preflight pulls exactly 15 proprietary images and atomically
writes a non-secret lock file with `tag@sha256` references. Combine that lock
file with `release/docker-compose.release.yml`; the overlay removes source
builds and forbids late pulls, so deployment consumes only the digests already
proved by the authenticated preflight. Never place the GHCR JSON value in
`infra/.env`, a container environment, a workflow payload, or release evidence.

Initial provisioning still uses the metadata startup script. PR1 stops at the
durable `foundation-ready/awaiting-runtime-authority` boundary with Docker
hard-fenced and publishes a durable receipt bound to the boot, instance,
startup bytes and watchdog evidence. The finalizer must not run during this
foundation apply. A later reviewed runtime-authority PR must prove the exact
runtime and all 26 image identities before adopting the marker and unmasking
Docker. Do not rerun startup as an ad-hoc deploy mechanism.

Before any Secret Manager access, startup also validates Terraform's five host
identity fields against the direct GCE metadata server and publishes canonical
schema-v1 JSON at `/etc/omega/gcp-host-identity.json`. Its exact keys are
`schema_version`, `project_id`, `instance_id`, `instance_name`, `zone`, and
`service_account_email`; the file is `root:root` mode `0400` under a
`root:root` mode `0755` directory. A pre-existing different file is a hard
failure, not an update path.
