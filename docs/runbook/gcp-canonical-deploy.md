# GCP canonical deploy — reproducible + fail-closed

How the canonical GCP VM (`omega-staging-app`) is actually deployed, and the
safe driver that codifies it. Reverse-engineered from the live host + the
Terraform startup script; before this it lived only in an operator's head.

> AWS is the DR standby. `scripts/deploy_main_aws.py` is the **separate** AWS path
> and must never run as the canonical writer (that would be split-brain).

## The live mechanism

The VM keeps immutable release trees and an atomic pointer:

```
/opt/modecissions/releases/<40-hex-sha>/     # extracted source tarball
/opt/modecissions/current -> releases/<sha>  # atomic symlink = what is live
/opt/modecissions/shared/infra.env           # the runtime env (secrets resolved on-host)
```

A release runs via three compose files, from the release's `infra/`:

```
docker compose --env-file infra/.env \
  -f infra/docker-compose.yml \
  -f infra/docker-compose.gcp.yml \
  -f infra/docker-compose.aws-images.gcp.yml --profile sap up -d
```

- `docker-compose.aws-images.gcp.yml` simply pins each of the 15 proprietary
  services to `ghcr.io/<owner>/<image>:<tag>` with `pull_policy: never`. Generating
  it for a new release is a **tag substitution** — nothing opaque.
- The persistent data disk is mounted at `/var/lib/docker`; the boot disk is
  ephemeral, so backups/state live under `/var/lib/docker/...`.
- The Terraform startup script (`infra/terraform-gcp/templates/startup.sh.tftpl`)
  stages the tarball + brings compose up, but does **not** run migrations and has
  **no backup/rollback** — the gap this driver closes.

## The driver

- `scripts/gcp/gcp-canonical-deploy.sh` — operator side (needs gcloud Owner + IAP):
  provenance check (tag→ref) → build+upload the exact source tarball → generate the
  tag-pinned image overlay → ship the remote deployer + overlay → run it → confirm.
- `scripts/gcp/gcp-canonical-deploy-remote.sh` — on-VM, fail-closed:
  1. stage `releases/<ref>/` and atomically hydrate the complete GCS HMAC and
     evidence-signing secret sets with
     `infra/terraform-gcp/release/hydrate-runtime-secrets.sh`;
  2. **authenticated pull** of 15 images at the tag (VM service account reads the
     GHCR pull credential from Secret Manager — nothing secret crosses the wire);
  3. start a short maintenance window: stop every `mode_*`/`omega_*` writer,
     fence both databases at `CONNECTION LIMIT 0`, terminate old sessions and
     prove that no competing sessions remain;
  4. take a **verified backup** of both DBs (`modecissions` +
     `modecissions_gold`) with sha256;
  5. recreate only the DB containers and run forward-only **migrations** with
     the checksum drift guard while all writers remain stopped;
  6. restore the original connection limits and compose up by pinned digest;
  7. **health gate** (version + app_env + readyz) and atomic promote of
     `current`. Rollback stops any candidate before restoring. The previous
     release starts only if exclusivity and every required restore succeeded;
     otherwise writers remain stopped for manual recovery. If resuming the
     previous release itself fails or starts only partially, the controller
     stops all writers again and reinstates both database fences.

`scripts/gcp/day2-release.sh` is intentionally disabled and exits with code 64.
It is not a second production path; use the canonical operator-side driver.

## Procedure (staged — do NOT skip the dry-run on a first run)

1. Confirm `origin/main` is the exact SHA to release; **cut the immutable tag** on it:
   `git tag vX.Y.Z-beta <sha> && git push origin vX.Y.Z-beta` → triggers the
   `Release Images` workflow (builds + pushes the 15 images at the tag).
2. Export the target (from Terraform): `OMEGA_PROJECT_ID`, `OMEGA_ZONE`,
   `OMEGA_INSTANCE`, `OMEGA_GHCR_OWNER`, `OMEGA_SOURCE_BUCKET`. Also create the
   required canonical
   GitHub Release assets:

   ```bash
   mkdir -p /tmp/omega-release-assets
   gh release download vX.Y.Z-beta \
     --pattern 'omega-release-manifest-vX.Y.Z-beta.json*' \
     --dir /tmp/omega-release-assets
   export OMEGA_RELEASE_MANIFEST=/tmp/omega-release-assets/omega-release-manifest-vX.Y.Z-beta.json
   export OMEGA_RELEASE_MANIFEST_CHECKSUM="${OMEGA_RELEASE_MANIFEST}.sha256"
   ```

   The local driver verifies the manifest's exact filename, checksum asset, v2
   schema, repository/owner, release identity and 15/15 immutable digests with
   the canonical release helper. It rebuilds the source archive from the exact
   commit and verifies the bytes at one immutable GCS generation; the VM fetches
   that same generation, checks SHA-256 before extraction and records a receipt.
   The remote deploy atomically hydrates runtime secrets. Rollback restores
   the prior environment file. This is the day-2 path for existing VMs;
   startup-script changes are intentionally ignored by Terraform.
3. **Dry-run** validates container discovery, tarball fetch, every required
   Secret Manager value in read-only `check` mode and the
   authenticated 15/15 pull. It exits before the maintenance window: it
   does not rewrite an env file, stop services, fence or dump a database,
   migrate, or swap releases.
   `OMEGA_DEPLOY_MODE=dryrun scripts/gcp/gcp-canonical-deploy.sh vX.Y.Z-beta <ref>`
4. If the dry-run is green, run the real deploy:
   `scripts/gcp/gcp-canonical-deploy.sh vX.Y.Z-beta <ref>`
5. Verify post-deploy: `/healthz` version, `/readyz`, and `/readyz?require_data=1`.

## Rollback

Automatic after any quiescence or mutation. If databases were mutated, the
candidate is stopped and both checksum-verified restores finish before the
previous release can start. A failed fence or restore leaves writers stopped;
perform manual recovery from
`/var/lib/docker/omega-deploy-backups/<tag>-<ref>-<ts>/` only after verifying
`SHA256SUMS` and confirming zero competing database sessions.

## Known gaps (P2 / follow-up)

- `docker-compose.gcp.yml` is carried from the running release; if a release needs
  a changed base overlay, regenerate it from Terraform `compose_override`.
- `--profile sap` and the `mode_*` container names are matched to the live host;
  a future compose refactor must update the driver's discovery in lock-step.
- Gold materialization (`readyz?require_data=1`) is a separate operational step
  (the "Sincronizar" flow), not part of this deploy.
- Rollback restores **data** and the schema present in the pre-migration dump
  atomically (`--single-transaction`); brand-new objects a failed forward
  migration created are not in that dump and persist harmlessly (migrations are
  `IF NOT EXISTS`, so a re-deploy re-applies cleanly). A true schema-level revert
  would drop+recreate the database first — deferred; the data is never lost.
