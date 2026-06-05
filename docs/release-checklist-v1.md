# v1.0 Public Release Checklist

Current status: NOT APPROVED for public v1.0.

Current `VERSION`: `1.45.3-beta`.

## Hard Rule

Do not change `VERSION` to `1.0.0` and do not create a final `v1.0` or
`v1.0.0` tag while any P2 item is BLOCKED.

Existing `v1.0.0-rc*` tags are release candidates only. They are not public
v1.0 approval.

## Required Green Gates

- P2-19 live readiness: stress/load without skip, multi-user simulation, and
  live LLM probe.
- P2-20 live cartridge validation: HubSpot, Salesforce, Replicon, SAP HCM,
  SAP S/4HANA, and SAP SuccessFactors live `test_connection` plus minimal
  extraction.
- P2-21 AWS/HTTPS staging: `make verify-v1-public` against public HTTPS URLs,
  Secrets Manager/SSM, OIDC-protected deploy, and tag rollback rehearsal.
- P2-22 Copilot/MCP threat model mapped to controls and tests.
- Full-stack release gate green on the final release tag.

## Current Blockers

| Item | Status | Evidence |
|---|---|---|
| P2-19 live readiness | BLOCKED | `docs/release-evidence/p2-19-live-readiness-blocked.md` |
| P2-20 live cartridges | BLOCKED | `docs/release-evidence/p2-20-live-cartridges-blocked.md` |
| P2-21 AWS/HTTPS staging | BLOCKED | `docs/release-evidence/aws-staging-checklist.md` |
| P2-22 threat model | DONE | `docs/security/copilot-mcp-threat-model.md` |

## Beta Debt To Close Before v1.0

| Item | Status | Evidence |
|---|---|---|
| P1-12 frontend component coverage | PARTIAL / DEUDA DE BETA | `docs/release-evidence/main-9a39a3026cd59b7030efc3e3d4c815f4362044e6.md#p1-12-frontend-coverage-status`; follow-up `FRONTEND-COV-001` |

## Final Tag Procedure

1. Rerun P2-19, P2-20, and P2-21 with real credentials/staging until all are
   green.
2. Save evidence under `docs/release-evidence/main-<sha>-v1.md`.
3. Verify `.github/workflows/release.yml` full-stack gate is green for the tag.
4. Update `VERSION` only after all P2 evidence is green.
5. Create final tag only after review:

```bash
git tag -a v1.0.0 -m "CONSOLA-BETA v1.0.0"
git push origin v1.0.0
```

## Current Verdict

- Beta privada/controlada: yes.
- v1.0 publica enterprise: no.
