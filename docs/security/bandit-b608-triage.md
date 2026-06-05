# Bandit B608 Triage Baseline

Date: 2026-06-05
Branch: `codex/audit-blockers-hardening`

## Command

```bash
.venv/bin/bandit -r console workspace vault refinement mcp-infra cartridges -t B608 -f json -q
```

## Current Result

Bandit currently reports 166 `B608` findings:

| Severity | Confidence | Count |
|---|---:|---:|
| MEDIUM | LOW | 127 |
| MEDIUM | MEDIUM | 39 |
| MEDIUM | HIGH | 0 |

The high-confidence security gate remains clean:

```bash
.venv/bin/bandit -r console workspace vault refinement mcp-infra cartridges --severity-level medium --confidence-level high
```

## Largest Buckets

| Count | File |
|---:|---|
| 27 | `refinement/app/duckdb_engine.py` |
| 20 | `console/app/routers/metrics.py` |
| 13 | `console/app/main.py` |
| 13 | `console/app/services/intelligence/persistence.py` |
| 5 | `console/app/routers/v1/pipeline_studio.py` |
| 5 | `console/app/services/cartridge_autopilot.py` |
| 5 | `mcp-infra/app/main.py` |
| 5 | `mcp-infra/app/tools/cartridges.py` |
| 5 | `refinement/app/main.py` |

## Policy

This baseline is not a pass for v1.0. Each B608 must be resolved before public
release by either:

- replacing string-built SQL with parameterized APIs or structured SQL helpers,
- proving identifier-only interpolation with a local validator and adding a
  tight `# nosec B608` justification at that line, or
- deleting the code path.

Until that adjudication is complete, CI fails on MEDIUM+ severity at HIGH
confidence and publishes the full JSON report for review.
