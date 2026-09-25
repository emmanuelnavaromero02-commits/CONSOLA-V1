# OMEGA Google Cloud Terraform

Separate Google Cloud deployment stack for OMEGA. This does not modify or depend on the AWS Terraform tree.

Initial beta shape:

- Compute Engine single-node host
- Docker Compose for the existing OMEGA services
- Private VM, IAP SSH, no public SSH
- External HTTP Load Balancer for the console
- Optional managed HTTPS Load Balancer for console, workspace, and Airflow
- Cloud Armor rate limiting
- GCS lakehouse and source buckets
- Secret Manager placeholders
- Billing budget alerts in the billing account currency

The technical HTTP IPs stay available as fallback. Set `public_console_domain`
and `public_workspace_domain` to create managed HTTPS, host rules, and
HTTP-to-HTTPS redirect on a dedicated public IP.

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
rollback. The preflight pulls exactly 16 proprietary images and atomically
writes a non-secret lock file with `tag@sha256` references. Combine that lock
file with `release/docker-compose.release.yml`; the overlay removes source
builds and forbids late pulls, so deployment consumes only the digests already
proved by the authenticated preflight. Never place the GHCR JSON value in
`infra/.env`, a container environment, a workflow payload, or release evidence.
