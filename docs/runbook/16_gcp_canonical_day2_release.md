# GCP canonical foundation transaction

This checkpoint keeps GCP as the only writer and scheduler. It performs the
single controlled writer-VM reset described below, but does not replace that
VM, promote AWS, start application containers, delete legacy edge resources,
or consume a private GHCR credential outside Secret Manager.

## Fixed authority and tooling

Use the reviewed official OpenTofu 1.11.6 binary. The transaction verifies the
platform-specific SHA-256 and executes a private copy. `GH_CONFIG_DIR` and
`GCP_CONFIG_DIR` must be absolute operator-owned, non-group/world-writable
directories. `GCP_OPERATOR_ACCOUNT` must be the one active gcloud account for
project `project-dd5ba7fa-374c-4554-ae6`.

Generate authority only from the exact approved source SHA and exact clean
controller SHA:

```sh
umask 077
make gcp-release-authority-generate \
  GCP_RELEASE_AUTHORITY=/absolute/private/release-authority.json \
  GCP_SOURCE_REF=<approved-main-sha> \
  GCP_CONTROLLER_REF=<clean-controller-sha> \
  GH_CONFIG_DIR=/absolute/operator/.config/gh \
  GCP_CONFIG_DIR=/absolute/operator/gcloud
```

The generator creates a mode-0400 schema-v2 receipt. It proves exact GitHub
attempt-1 gates, GCS object generation/size/checksums, VM identity, immutable
boot image, host package lock, secret version IDs, current startup bytes, and
tool identities. It repeats the live VM, secret and GitHub projections before
returning. Revalidate immediately before each plan:

```sh
make gcp-release-authority-verify \
  GCP_RELEASE_AUTHORITY=/absolute/private/release-authority.json \
  GH_CONFIG_DIR=/absolute/operator/.config/gh \
  GCP_CONFIG_DIR=/absolute/operator/gcloud
```

Do not hand-write, pretty-print, copy, or amend this receipt.

## Adopt the three pre-existing secret containers

Before the foundation plan, run one separate `secret-adoption` transaction.
It targets exactly the three pre-existing Control Room evidence secret
containers and permits only their config-driven imports plus the reviewed
label transition. It neither reads nor imports secret versions or payloads.

```sh
make gcp-terraform-plan \
  GCP_TERRAFORM_PROFILE=secret-adoption \
  GCP_TRANSACTION_DIR=/absolute/private/adoption-transaction \
  GCP_TFVARS=/absolute/private/staging.tfvars \
  GCP_RELEASE_AUTHORITY=/absolute/private/release-authority.json \
  TOFU=/absolute/reviewed/tofu \
  GCP_OPERATOR_ACCOUNT=<operator-account> \
  GH_CONFIG_DIR=/absolute/operator/.config/gh \
  GCP_CONFIG_DIR=/absolute/operator/gcloud
```

Independently review the sealed `manifest.json` and `plan.json`, then use the
same variables with `make gcp-terraform-apply`. A full foundation plan rejects
unconsumed imports and cannot recreate those three containers.

## Foundation plan and apply

`make gcp-foundation-plan` creates a new mode-0700 transaction directory; the
path must not exist. The command fixes `refresh=true`, derives JSON and startup
bytes from the same held plan descriptor, validates exact actions and drift,
and seals source, config, tool, authority, plan and startup identities. It
accepts no caller-supplied plan JSON, target, replace, destroy or refresh-only
flags.

After independent review, use `make gcp-foundation-apply` with the same absolute
inputs. Immediately before mutation it repeats the live authority and six-hour
freshness gates, records the pre-apply Terraform state lineage/serial/hash, and
fsyncs `apply-intent.json`. Its append-only states are:

- `apply-intent.json`: mutation may have started;
- `apply-result.json`: OpenTofu exited zero; postcheck may still be pending;
- `apply-failure.json`: outcome is `INDETERMINATE`;
- `apply-receipt.json`: apply and postcheck both passed.

For this profile, "postcheck" is intentionally broader than edge TLS. The sole
canonical controller performs these phases in order:

1. apply the exact saved foundation plan;
2. install the sealed `startup-script.sh` with a live Compute metadata
   fingerprint CAS while preserving the exact metadata inventory;
3. fsync a reset intent, request at most one VM reset, and prove a new start
   timestamp with unchanged startup bytes;
4. collect `/opt/modecissions/shared/foundation-receipts/<controller-ref>.json`
   over IAP through the installed root-owned safe-I/O verifier;
5. verify the receipt's instance ID, zone, boot ID, controller/source refs,
   startup/config hashes and marker/watchdog hashes, then re-read the VM;
6. run the post-transition edge/TLS gate, repeat the live VM/current-boot host
   receipt read-back, and seal all four startup evidence files before writing
   `apply-receipt.json`.

The embedded `scripts/gcp/startup_metadata_transaction.py` is not a second
operator interface and must never be invoked directly. Terraform deliberately
retains `ignore_changes = [metadata_startup_script]`: provider ownership is
ForceNew and would replace the canonical writer. The sealed CAS controller is
the only startup metadata writer.

Inspect without mutation:

```sh
make gcp-foundation-status \
  GCP_TRANSACTION_DIR=/absolute/private/foundation-transaction
```

`make gcp-foundation-recover` performs postcheck only when a durable successful
Terraform result exists. It never reruns a saved plan. A durable metadata CAS
intent permits recovery only from the exact candidate bytes. A durable reset
intent never permits another reset: recovery waits for/proves the changed boot
and exact server handoff, or remains failed closed for operator reconciliation.
Intent without Terraform result, or an explicit Terraform failure, is a hard
stop: reconcile the remote lock/state and live resources, preserve all
receipts, and produce a fresh reviewed plan. Never delete `apply-data-*` or
transaction evidence to force a retry.

Startup publishes `/etc/omega/gcp-host-identity.json` before any secret access.
It is canonical schema-v1 JSON with exactly `schema_version`, `project_id`,
`instance_id`, `instance_name`, `zone`, and `service_account_email`; all five
identity values must match both the Terraform-rendered contract and direct GCE
metadata. The path is `root:root` mode `0400`, its directory is `root:root`
mode `0755`, and a conflicting pre-existing identity fails the boot.

## Reversible project-wide IAM revoke

The legacy project-wide Secret Manager accessor deletion is not a normal
foundation apply. Create an empty mode-0700 outer directory and use only:

```sh
install -d -m 0700 /absolute/private/iam-revoke-transaction
make gcp-iam-revoke-plan \
  GCP_IAM_TRANSACTION_DIR=/absolute/private/iam-revoke-transaction \
  GCP_TFVARS=/absolute/private/staging.tfvars \
  GCP_RELEASE_AUTHORITY=/absolute/private/release-authority.json \
  TOFU=/absolute/reviewed/tofu \
  GCP_OPERATOR_ACCOUNT=<operator-account> \
  GH_CONFIG_DIR=/absolute/operator/.config/gh \
  GCP_CONFIG_DIR=/absolute/operator/gcloud
```

The outer controller proves the six resource-scoped grants and the live
effective-access matrix before arming mutation. The inner exact plan may
delete only the unconditional project-wide accessor grant. Generic IAM apply
is locked to the active outer controller and its exact mutation-intent event.
After delete, the controller requires all six grants, checks all 64 forbidden
secret resources, and proves zero forbidden access without reading values.

On apply error, failed postcheck, signal, or a dangling intent, the controller
restores the broad grant and verifies the grants matrix. Use
`make gcp-iam-revoke-recover` after interruption. A restored transaction is
terminal and requires a new sealed Terraform reconciliation; do not reuse its
inner plan.

## Foundation handoff

The startup controller may terminate only as verified live recovery or as
`foundation-fenced`. The latter keeps Docker and containerd hard-fenced and
publishes an append-only receipt under
`/opt/modecissions/shared/foundation-receipts/`. That receipt binds the exact
startup bytes, instance/zone, boot ID/start epoch, controller/source refs, and
terminal marker/watchdog evidence.

PR1 stops at this durable `foundation-ready/awaiting-runtime-authority`
boundary. Do not run `finalize-startup-adoption.sh` as part of this apply. A
later reviewed runtime-authority operation must prove the exact release and
all 26 local image identities before adopting the marker and unmasking Docker.

The failed direct Compute certificate, legacy technical IPs/proxies/rules and
legacy workspace map remain declared. Cleanup is a separate irreversible
change and is prohibited here.
