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
The token is a server credential limited to `read:packages`; it must not
receive repository `contents` access or any package-write scope. Neither
Terraform nor the release controller creates or selects a secret version. The
reviewed bundle installer validates the provisioned host identity, then
append-only installs root-owned mode-`0400`
`/etc/omega/ghcr-pull-secret-authority.json`, fixing the canonical secret
resource and Secret Manager alias `active` without caller-provided authority.
If the identity or alias is absent or inaccessible, the pull path stops
fail-closed.
One non-truncating host lock, `/run/.omega-gcp-ghcr-release.lock`, serializes
the canonical operation from authentication through preflight; its parent,
auth root, config, context, and lock remain bound by inherited descriptors.

`release/ghcr-auth-run.sh` resolves `active` from Secret Manager and requires
the response to name one exact numeric version. An optional
`OMEGA_GHCR_PULL_SECRET_VERSION` is only an equality assertion; it never
selects the version. The resolved numeric resource is recorded in the
root-owned mode-`0400` ephemeral auth-context receipt. Before requesting a
service-account token, the runner compares the project,
instance ID/name, zone, and service-account email from metadata against the
canonical root-owned mode-`0400` `/etc/omega/gcp-host-identity.json` installed
by bootstrap. Ambient environment values are not host identity. The wrapper
accepts only its exact sibling `preflight-release-images.sh` and the five
release-authority arguments; it cannot execute a caller-supplied command.
Invoke it as
`ghcr-auth-run.sh preflight-release-images.sh OWNER TAG SHA VERSION LOCK`.
Docker authentication remains in a temporary host-only `DOCKER_CONFIG`, and
Docker login/info/pull/inspect operations have hard process-group deadlines.
The preflight pulls exactly 15 proprietary images and
crash-consistently publishes a non-secret authority, digest lock, and commit
marker (the lock contains only `tag@sha256` references) inside a
revision-bound `.SHA.tmp.PID` staging directory. The day-2 controller is the
only component allowed to publish that staged directory as the final lock.
Published pulls additionally prove the raw annotated Git tag object, its exact
commit, and its sealed-manifest binding without a `.git` directory, a GitHub
contents credential, or a moving `main`. The release workflow produces the
attempt-namespaced `release-tag-proof-<source-sha>-<run-attempt>` artifact. The
canonical GCP controller installs its single `annotated-tag.object` file as
root-owned mode `0400`
at `/opt/modecissions/shared/release-authority/<source-sha>/annotated-tag.object`.
The deployment source itself must be a `git archive` of the approved SHA, not a
private clone. Set `OMEGA_RELEASE_ANNOTATED_TAG_OBJECT_FILE` to that exact path
and invoke both boundaries with `/bin/bash -p` from a clean environment. The
host verifier recomputes the Git tag-object SHA and accepts one direct commit,
tag name, and manifest binding only. Combine the published lock with
`release/docker-compose.release.yml`;
the overlay removes source builds and forbids late pulls, so deployment
consumes only the digests already proved by the authenticated preflight. Never
place the GHCR JSON value in
`infra/.env`, a container environment, a workflow payload, or release evidence.
