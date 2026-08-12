# GCP private-GHCR release bundle contract

This contract is consumed by both `scripts/gcp/image-preflight.sh` and
`scripts/gcp/day2-release.sh`.  Neither controller may install only the auth
runner or invoke helpers from an extracted release tree.

1. Run the installer as root from either of these two authenticated source
   forms:

   - pre-tag preflight: the checksum/generation-verified artifact is safely
     extracted into the root-owned, mode-`0700` ephemeral directory
     `<workdir>/<helper-sha>`;
   - day-2: the immutable published release directory
     `/opt/modecissions/releases/<helper-sha>`.

   The source directory's basename must be the exact 40-hex helper SHA.  The
   preflight source is temporary and must never be published below
   `/opt/modecissions/releases`; the sealed bundle is its only durable output.
   Invoke:

   ```text
   /usr/bin/python3 -I infra/terraform-gcp/release/install-ghcr-release-bundle.py \
     --release-root <authenticated-source>/<helper-sha> \
     --source-sha <helper-sha>
   ```

   The only accepted output is
   `/opt/modecissions/shared/ghcr-release-bundles/<helper-sha>`.  Publication is
   one rename from `.helper-sha.tmp.PID`; the final directory is root-owned
   mode `0500`.  It contains the runner, secure session helper, preflight,
   both validators, publisher, `release_images.py`, and the generated bundle
   manifest.  Controllers invoke the runner and its exact sibling preflight
   from that directory.  They never rename either entrypoint.

2. Every candidate, published, and legacy-rollback pull writes initially to
   this exact root-owned mode-`0700` staging hierarchy:

   ```text
   /opt/modecissions/shared/image-locks/.<target-sha>.tmp.<pid>/release-images.env
   ```

   Only the controller may rename a successfully committed staging directory
   to its durable semantic destination.  `/tmp`, a final directory, or an
   evidence directory is never an auth-runner output.

3. A published pull requires the raw Git annotated-tag object at exactly:

   ```text
   /opt/modecissions/shared/release-authority/<target-sha>/annotated-tag.object
   ```

   The parent is root-owned mode `0700`; the single-link file is root-owned
   mode `0400`.  The controller sets
   `OMEGA_RELEASE_ANNOTATED_TAG_OBJECT_FILE` to that exact path together with
   `OMEGA_RELEASE_TAG_MANIFEST_SHA256` and `OMEGA_RELEASE_TAG_OBJECT_SHA`.
   Candidate and legacy-rollback calls must not set the annotated-tag path.

4. The selected Secret Manager version is server authority, not caller
   authority.  `/etc/omega/ghcr-pull-secret-authority.json` is canonical JSON,
   root-owned mode `0400`, and contains exactly `schema_version`,
   `environment`, `project_id`, `secret_id`, and
   `secret_version_alias:"active"`.  The runner resolves that GCP-native alias
   and accepts only a response whose resource ends in numeric `versions/N`;
   that exact resource is written into the root-owned mode-`0400` ephemeral
   auth-context receipt. `OMEGA_GHCR_PULL_SECRET_VERSION` is optional
   compatibility input; when set, it must equal resolved `N` and cannot select
   another version. The credential itself remains only in Secret Manager and
   the temporary `/run` session.

   `install-ghcr-release-bundle.py` validates the exact provisioned
   `/etc/omega/gcp-host-identity.json` before append-only publication of this
   non-secret authority. Its CLI accepts no project, environment, secret, or
   version selector, so these values remain server-owned.

The auth runner binds the complete installed tree to inherited read-only file
descriptors before reading metadata or Secret Manager.  Docker is also opened
and executed through its validated descriptor.  A bounded child is successful
only after output limits, its deadline, and complete process-group teardown
have all been proved.
