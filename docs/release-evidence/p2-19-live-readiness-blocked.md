# P2-19 Live Readiness Evidence

Status: BLOCKED

Branch: `codex/v1-hardening-gates`

## Implemented Gate

`make v1-live-readiness` runs `scripts/production_readiness.sh` with:

- `OMEGA_PRODUCTION_READINESS_V1=1`
- stress required; `OMEGA_PRODUCTION_READINESS_SKIP_STRESS=1` is refused
- multi-user simulation required
- live LLM probe required
- Locust live LLM probe required

## Acceptance Command

```bash
make v1-live-readiness
```

## Output

```text
[production-readiness] v1 live readiness enabled: stress, multi-user simulation, and live LLM probes are required
[production-readiness] BLOCKED: E2E_ADMIN_PASSWORD or TEST_PASSWORD is required for OMEGA_PRODUCTION_READINESS_V1=1
make: *** [v1-live-readiness] Error 2
```

## Unblock

Set the bootstrap admin credential for the running stack and rerun:

```bash
E2E_ADMIN_PASSWORD=<bootstrap-admin-password> make v1-live-readiness
```

The command must pass without `OMEGA_PRODUCTION_READINESS_SKIP_STRESS=1`.
