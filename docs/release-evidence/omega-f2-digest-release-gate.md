# OMEGA F2 — digest release gate

Status: **PREPARED**. F2 remains open until the GitHub workflow completes on the
merge SHA, all 15 private image digests are verified, and the immutable GitHub
Release for `v1.45.210-beta` is published and re-attested.

## Audited local authority

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

To close F2, record the merge SHA and require the live release workflow to prove:

1. the immutable Git tag equals that merge SHA and `VERSION=1.45.210-beta`;
2. all 15 GHCR packages remain private and every manifest/config/layer exists;
3. the mandatory digest stack gate passes for the exact manifest bytes;
4. the final GitHub Release is non-draft and immutable, with exactly the
   canonical manifest and checksum assets; and
5. both assets and all 15 remote digest graphs still match after publication.
