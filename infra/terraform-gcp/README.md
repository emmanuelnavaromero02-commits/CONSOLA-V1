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

GCS HMAC lakehouse credentials are read from Secret Manager secrets
`omega-<env>-gcs_hmac_access_key_id` and
`omega-<env>-gcs_hmac_secret_access_key`. This stack creates the secret
containers but does not create HMAC keys, because some GCP organizations block
service-account key creation with `iam.disableServiceAccountKeyCreation`.
