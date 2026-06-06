# v1 GA Unified Stress + Offensive Security Harness

This runbook covers the single v1 GA harness. It replaces separate "stress" and
"security gauntlet" plans: every mode runs one A-I sequence and writes evidence
under `docs/release-evidence/v1-stress/<run_id>/<lane>/`.

## Commands

```bash
make v1-ga-lite-local
PUBLIC_CONSOLE_URL=http://modecissions-public-255609366.us-east-1.elb.amazonaws.com make v1-ga-lite-aws
PUBLIC_CONSOLE_URL=https://staging.example.com \
  OMEGA_V1_STRESS_TARGET=staging \
  OMEGA_V1_STRESS_ALLOW_CHAOS=1 \
  OMEGA_V1_GA_DEDICATED_STAGING=1 \
  make v1-ga-max-aws
make v1-ga-cleanup
make v1-ga-report
```

## Modes

- `v1-ga-lite-local`: validates the harness locally. It runs `make smoke`,
  `make e2e`, `make acceptance`, `OMEGA_PRODUCTION_READINESS_SKIP_STRESS=1 make
  production-readiness`, reduced multi-user isolation, security gauntlet,
  Control Room checks, MCP/Copilot policy checks, beta stress, DB audit guards,
  and cleanup proof.
- `v1-ga-lite-aws`: validates the deployed AWS URL without destructive chaos.
  It probes `/healthz`, `/readyz`, and `/readyz?require_data=1`, then runs the
  same unified package where credentials are available. Missing DB credentials
  are recorded as `BLOCKED`, not `PASS`.
- `v1-ga-max-aws`: GA-grade staging run. It refuses to start unless the target is
  dedicated staging, chaos is explicitly allowed, `VERSION` is still beta/rc,
  and at least 20GB of disk is free. Missing Anthropic or live cartridge
  credentials block the live phases instead of being mocked as done.

## Phases

- A: seed massivo / controlled STRESS data
- B: isolation matrix and multi-tenant security
- C: offensive security gauntlet
- D: concurrent soak
- E: Control Room E2E
- F: Copilot + MCP adversarial
- G: custom cartridge factory
- H: chaos / fault injection
- I: DB audit, report, and cleanup

## Decision Rule

- Beta fuerte: `v1-ga-lite-local` PASS and `v1-ga-lite-aws` PASS with zero leaks
  and zero successful attacks.
- 1.0 RC: the above plus `v1-ga-max-aws` mostly PASS, with live credentials
  explicitly `BLOCKED` if unavailable.
- 1.0 public: `v1-ga-max-aws` PASS with no critical skips: 4h stress, live LLM,
  at least one real sandbox cartridge, chaos in dedicated staging, DB/RLS audit,
  and cleanup.
- Any cross-tenant leak is STOP. Keep `VERSION` beta and open a fix.
