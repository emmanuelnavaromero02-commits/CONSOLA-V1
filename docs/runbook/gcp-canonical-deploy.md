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
  1. **verified backup** of both DBs (`modecissions` + `modecissions_gold`) with
     sha256 — before any mutation (the safety net the live path lacks);
  2. stage `releases/<ref>/` (exact tarball + carried env/overlay + new image overlay);
  3. **authenticated pull** of 15 images at the tag (VM service account reads the
     GHCR pull credential from Secret Manager — nothing secret crosses the wire);
  4. forward-only **migrations** with the checksum drift guard;
  5. compose up by pinned tag; 6. **health gate** (version + app_env + readyz);
  7. atomic promote of `current`. Any failure after step 4 rolls back to the
     previous release and restores both DBs from the verified backup.

## Procedure (staged — do NOT skip the dry-run on a first run)

1. Confirm `origin/main` is the exact SHA to release; **cut the immutable tag** on it:
   `git tag vX.Y.Z-beta <sha> && git push origin vX.Y.Z-beta` → triggers the
   `Release Images` workflow (builds + pushes the 15 images at the tag).
2. Export the target (from Terraform): `OMEGA_PROJECT_ID`, `OMEGA_ZONE`,
   `OMEGA_INSTANCE`, `OMEGA_GHCR_OWNER`, `OMEGA_SOURCE_BUCKET`.
3. **Dry-run** (non-destructive — validates container discovery, backup non-empty,
   tarball fetch, and the authenticated 15/15 pull, without migrating or swapping):
   `OMEGA_DEPLOY_MODE=dryrun scripts/gcp/gcp-canonical-deploy.sh vX.Y.Z-beta <ref>`
4. If the dry-run is green, run the real deploy:
   `scripts/gcp/gcp-canonical-deploy.sh vX.Y.Z-beta <ref>`
5. Verify post-deploy: `/healthz` version, `/readyz`, and `/readyz?require_data=1`.

## Rollback

Automatic on any failure after migrations. Manual: re-point `current` to the
previous `releases/<sha>` and `compose up`, then restore from the retained
`/var/lib/docker/omega-deploy-backups/<tag>-<ref>-<ts>/` (verify SHA256SUMS first).

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
