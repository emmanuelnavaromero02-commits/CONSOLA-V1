# OMEGA F2 — digest release gate

Status: **PREPARED** for forward recovery as `v1.45.216-beta`; F2 is not closed.
The immutable `v1.45.210-beta` through `v1.45.215-beta` attempts are retained
as failed evidence, and no tag is moved or reused.

## Failed live attempt retained

- Workflow run `31801477645` for `v1.45.210-beta` failed in
  `validate-release` with `13 failed, 689 passed, 18 errors`.
- The annotated tag object remains
  `6c70e0067eb44dd991d355b3e5cab663300c790b`; it peels to source SHA
  `2429e9a2bdab13ff00740fe318009fd5b101850d`.
- Workflow run `31809737977` for `v1.45.211-beta` also failed in
  `validate-release` with `1 failed, 720 passed, 16 errors`:
  MinIO was not precached for pull-never staged-publication tests and the
  Refinement DuckDB extension cache was not prepared for the live Gold test.
- The `.211` annotated tag object remains
  `cadf0b28b771257bc6cb9129cf8b4cd72ef5adff`; it peels to source SHA
  `cc0873e4d86bb5bd2f183a003d43ff8a0970df8c`, whose sole parent is the
  `.210` source SHA.
- Both jobs failed before publication fan-out: downstream jobs were skipped and
  no preflight, image build, manifest, digest gate, or release assets ran.
- Published outputs for each attempt were zero: workflow artifacts `0`, GHCR
  candidates `0`, and canonical GitHub Release `0`.
- Workflow run `31823738299` for `v1.45.212-beta` passed validation, package
  privacy preflight, all 15 image builds, and canonical manifest assembly. It
  then failed closed in `digest-full-stack-gate`, at
  `Freeze trusted Playwright and Docker gate runtimes`: preserved private mode
  bits made
  `/opt/omega-release-runtime/browsers/chromium-1223/chrome-linux64/deb.deps`
  unreadable after root ownership, and the runtime hash raised
  `PermissionError`.
- The `.212` annotated tag object is
  `5e3bbc3d1475486bbc0ddabb57b440210ac0782c`; it peels to source SHA
  `dd882bc08bb445d1446f9cbbe313448b24827720`, whose sole parent is the
  `.211` source SHA.
- The `.212` run retained exactly 16 workflow artifacts: 15 image-candidate
  receipts plus `omega-release-manifest-31823738299-1`. Manifest binding,
  all 15 digest pulls, stack startup, final gates, and publication were skipped;
  `publish-release-manifest` was skipped and the tag-addressable GitHub Release
  endpoint remained `404`, with zero Release assets.
- Workflow run `31831837077` for `v1.45.213-beta` proved the `.212` permission
  repair in Linux: validation, package preflight, all 15 builds, manifest
  assembly, the trusted runtime freeze, and all 15 exact digest pulls passed.
  It then failed closed while pulling auxiliary infrastructure because the
  standard runner exhausted disk space after Superset, while registering a
  MailHog layer: `no space left on device`.
- The `.213` annotated tag object is
  `926330d8e1e2067e4e56429fb91e4585ffa4eb43`; it peels to source SHA
  `4bcfda1811d4cbe0511624e0d5cd9c1f5205926b`, whose sole parent is the
  `.212` source SHA.
- The `.213` run retained 15 private tagless candidate receipts plus one
  canonical manifest. The digest stack never started, all final gates and the
  publisher were skipped, and the GitHub Release remained absent.
- Workflow run `31843803006` for `v1.45.214-beta` passed validation, the package
  privacy preflight, all 15 image builds, canonical manifest assembly, the
  trusted runtime freeze, all 15 exact digest pulls, and the runner disk
  boundaries. It then failed closed during stack startup with
  `dependency failed to start: container mode_airflow is unhealthy`.
- The `.214` annotated tag object is
  `cea2ec51314248daf26710cfe8a94a483d7287eb`; it peels to source SHA
  `325170ff109e88860df857c8615f05b059698fec`, whose sole parent is the
  `.213` source SHA.
- The `.214` run retained exactly 16 workflow artifacts: 15 private tagless
  candidate receipts plus the canonical manifest. The publisher
  `publish-release-manifest` was skipped; there was no GitHub Release and no
  canonical `.214` GHCR tag.
- Workflow run `31851541639` for `v1.45.215-beta` passed validation, package
  privacy preflight, all 15 image builds, canonical manifest assembly, the
  trusted runtime freeze, runner-disk reclaim and all three disk budgets, all
  15 exact digest pulls, auxiliary infrastructure pulls, and Compose startup.
  Both Airflow webserver and scheduler were healthy in the final container
  diagnostics.
- The `.215` run then failed closed in `Wait for exact digest stack basic
  readiness` after 240 seconds. The job exported `COMPOSE_FILE=""`, while
  `wait_for_health.sh` replaced that empty exported value with its default
  path. The post-lock Docker guard therefore returned exit 97 for every
  health inspection with `Docker control environment is forbidden after
  release lock: COMPOSE_FILE`; suppressed inspection errors were reported as
  false `starting` states even though the stack was healthy.
- The `.215` annotated tag object is
  `f40a3ab516689343514411806318cffd4f67c3bd`; it peels to source SHA
  `82a7e45adff10b4877b1bfb0e6acab4206744c60`, whose sole parent is the
  `.214` source SHA. The run retained exactly 16 workflow artifacts. Its
  canonical manifest SHA-256 was
  `3259df40b06c312b39ad4d8ad0c1731e98b2f2f986165b850d7e766c3ff54393`;
  all 15 candidate images remained private and tagless (`tags: []`), and an
  exhaustive GHCR audit found zero canonical `v1.45.215-beta` tags across the
  15 packages. Final readiness/E2E/stress, digest re-verification, and the final
  harness check were skipped; `publish-release-manifest` was skipped and the
  tag-addressable GitHub Release remained `404`.
- The forward-only recovery target is `v1.45.216-beta`. Its previous-release
  selector may bridge to exact `v1.45.209-beta` only when all six failed
  annotated tag objects and peeled SHAs remain exact, the
  `.210 -> .211 -> .212 -> .213 -> .214 -> .215 -> .216` direct parent chain remains
  exact, and none of the failed markers has canonical release evidence. Exact
  remote tag refs are rebound atomically into a dedicated authority namespace
  before selection; a missing, moved, lightweight, swapped, ambiguous, or
  unexpectedly trusted failed marker blocks recovery.

## Retained `.210` prepared authority

- Source baseline: `48d29731c0dd488d84dac5d2efc2c7b45ed2b977`.
- Audited F2 freeze: `82a064a43f7ac00573affaf5b196ac255985a46e`.
- Freeze tree: `b9d0a09ea8681e1b4f40a79450400162f28f0719`.
- Freeze patch SHA-256: `a31fa4599567f496f91a3ccbe80abfa971cd045f881bf1b938984ac2c1989f0f`.
- Harness seal SHA-256: `f940c6e2184ea1e786c8391903d15523eb0cf4731d7928f38e2f7881a5ba4adc`.
- Harness inventory: 1,337 files; authorized skips: 61 pytest declarations
  and 27 Playwright declarations.

## Local verification

- Release contract suite: 432/432 passed.
- Independent release regression audit: 504/504 passed.
- Docker lock on Linux: 108/108 passed.
- Exact Compose consumers under the real lock: 7/7 passed (three MCP
  configurations, two AWS evidence cases, and two Gold repair cases).
- The Gold project left zero containers, networks, or volumes; the Docker image
  inventory was byte-identical before and after the run.
- Ruff, Python compilation, workflow YAML, 41 shell run blocks, diff checks,
  harness verification, and skip-policy verification passed.
- Two independent adversarial reviews reported zero P0/P1 findings on the
  audited freeze.

## Forward-recovery patch verification

- The focused `.212` selector, workflow, Control Room, and DuckDB cache matrix
  passed `102/102` tests. The expanded release/control regression passed
  `458/458` tests.
- Real DuckDB 1.2.2 image probes on both Linux architectures produced the exact
  sealed cache topology: `httpfs` and `postgres_scanner`, each with its `.info`
  file, under `linux_arm64` or `linux_amd64_gcc4`; no extra file or symlink was
  present. The MinIO preload was also checked by exact tag-at-digest and image
  identity.
- The regenerated harness seals `1,339` files with SHA-256
  `716c68f45a84f0e93646281270c4a4757858faafd25424f75fba394c63f638b8`;
  the skip inventory remains exactly 61 pytest and 27 Playwright declarations.
- Live read-only checks confirmed run `31801477645` has zero artifacts, all
  downstream publication jobs were skipped, and the `.210` Release endpoint is
  `404`.
- Live read-only checks confirmed run `31809737977` also has zero artifacts,
  zero new versions across all 15 GHCR packages, all downstream publication
  jobs skipped, and no listed or tag-addressable `.211` Release.
- The `.213` recovery normalizes the copied Playwright runtime before hashing:
  directories become traversable read-only (`0555`), regular files become
  readable read-only (`0444`), and only files that were executable retain
  execute permission (`0555`). The normalizer does not follow symlinks and
  blocks escaping symlinks or special files before changing any mode.
- The `.213` selector plus release/runtime permission contracts passed `95/95`
  focused tests. The expanded release, GitHub Release, Control Room CI,
  Refinement, selector, and permission regression passed `505/505` tests.
- The regenerated `.213` harness seals `1,341` files with SHA-256
  `48944574b1ed55041b553f70cea9ea32207553b7ef7650a15aa5ceda96f6bce7`;
  the skip inventory remains exactly 61 pytest and 27 Playwright declarations.
- The final `.214` harness seals `1,344` files with SHA-256
  `16d023b879be6c7d22a6273859c37f306a88083359d1c853e6c03abac859b6e8`.
- The prepared `.215` harness seals `1,345` files with SHA-256
  `cbe7dd5128a22cc6dbbf7deaa529b104840f405b4065d1f1bfdc4050ebe8f8c4`.
- The `.216` repair keeps the fallback Compose path in a non-exported local
  variable and unsets only an exported empty `COMPOSE_FILE` before the first
  Docker inspection. A genuinely non-empty control value remains poisoned and
  now propagates the lock's exit 97 immediately, without a readiness timeout or
  false `starting` state.
- The `.216` focused selector, CI, and independent adversarial acceptance
  matrix passed `135/135`; the pre-seal expanded release matrix passed
  `539/539`. Ruff, Python compilation, workflow YAML, all 50 Bash run blocks,
  the standalone shell syntax check, diff checks, and the skip-policy verifier
  also passed.
- The prepared `.216` harness seals `1,347` files with SHA-256
  `ee0a442505a6532bcf2853d73ce7cccef83119382b29d4647ebc26b47baedb9c`.
- The `.214` runner-disk contract is based on the actual `linux/amd64` OCI
  footprint: 12.468 GiB extracted and 4.004 GiB compressed after layer
  deduplication. It requires 17 GiB free before the 15 application pulls,
  5 GiB before auxiliary infrastructure, and 2 GiB before Compose startup;
  every boundary also requires at least 100,000 free inodes. Cleanup is
  adaptive and limited to the copied Playwright installer, pip/npm caches,
  Android, and CodeQL on a digest gate pinned to GitHub-hosted Ubuntu 24.04.
  It never prunes Docker or removes Python, Node, or Docker.
- `.github/workflows/release-runner-capacity.yml` reproduces the dependency and
  Chromium footprint on the same pinned runner, then executes the source-bound
  cleanup helper. Its PR and post-merge runs must pass before the immutable
  `.214` tag is created; this proves the 17-GiB boundary on the live image
  rather than assuming capacity from runner documentation.

## F2 contract

- Builds produce 15 private, untagged GHCR candidates addressed only by digest.
- The canonical v2 manifest binds repository, immutable Git tag, source SHA,
  workflow run, service identity, media type, size, and all 15 digest references.
- The digest full-stack gate is mandatory before publication and binds the full
  26-service Compose model plus the 17 runtime services to the 15 image digests.
- Publication uses a recoverable draft, re-downloads and compares both assets,
  revalidates the remote OCI graphs, and requires an immutable GitHub Release.
- The authenticated Release inspector handles drafts through the paginated
  release list and published releases through the tag endpoint, failing closed
  on duplicate, malformed, or racing identities.

## Deliberate boundary and live closure

F2 certifies exact image digests together with the exact source-checkout bind
mounts. It does not claim image-only runtime, bit-for-bit rebuildability, SBOM,
or signed provenance; those remain later-phase work. The sealed, reviewed test
harness is an authority boundary, not a sandbox against deliberately malicious
same-UID or privileged code.

To close F2, record the recovery merge SHA and require the live release workflow
for `v1.45.216-beta` to prove:

1. the immutable Git tag equals that merge SHA and `VERSION=1.45.216-beta`;
2. all 15 GHCR packages remain private and every manifest/config/layer exists;
3. the mandatory digest stack gate passes for the exact manifest bytes;
4. the final GitHub Release is non-draft and immutable, with exactly the
   canonical manifest and checksum assets; and
5. both assets and all 15 remote digest graphs still match after publication.
