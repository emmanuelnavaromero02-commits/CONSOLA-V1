# Operational truth: data integrity

Correction start: `ff46d2a5d8cc077c55957d47d64ccee60f95af3b`

Branch: `fix/operational-truth-data-integrity`

## What changed

- The packaged SuccessFactors talent benchmark is an enabled but unreviewed
  `system_default`. It is not sectorial, external, learned, or approved.
- Talent readiness still computes the tenant/workspace internal raw percentile,
  but it cannot emit a benchmark score or readiness classification unless an
  approval has a non-system actor and a real timestamp.
- Control Room no longer creates the fixed `92/68/45` options. Analytical
  options require explicit identity, label, action, and a finite score; missing
  numeric inputs are not filled with zero and no option is auto-selected.
- Replicon WIP retains real USD and MXN invoice totals separately. Converted
  amounts, WIP, completion, and status exist only with positive finite billing
  rates and positive finite FX. Null/non-finite amounts, hours, durations, and
  billing flags fail closed. Missing FX is `missing_fx`; other incomplete inputs
  are `insufficient_data`, with converted values and financial signals null.
- `manual_fixture` is available only when `APP_ENV` is explicitly `test`,
  `local`, or `development`, including the engine executor's preflight.
  Standalone feature flags cannot enable it.
- Every Monte Carlo input distribution is persisted as a scenario assumption,
  not as an observation. Manual fixtures are additionally `not_calibrated`.
- A new Gold filename repairs the separate `modecissions_gold` database through
  the real runner. The repair is atomic and is not registered after failure.
- Replicon WIP materializations now have package/SQL/input digests, immutable
  history, a generation-checked current head, and quarantine for legacy FX.
  Public views are security-invoker views over the exact current head; history
  is forced through workspace RLS and the runtime role cannot read quarantine.
- Base currency is server-owned and effective-dated. Original amounts and their
  real currency remain visible; converted values remain null on missing FX.
- Calibration provenance is resolved transitively. Recompute uses deterministic
  keyset pages, exact accounting, and the same transaction lock as observe.
- Wisdom sources are reloaded from durable server-owned rows. Historical
  executions revalidate every source before candidate, execution, plan, or
  posterior writes; missing/manual/fixture/mock/synthetic ancestry fails closed.
- Monte Carlo rejects unknown/non-applicable variables and client model-version
  overrides. Common random numbers are stable by seed and variable name; tied
  options are ambiguous and have no selected winner.
- Replicon financial alerts require `financial_status=ready`; persisted or
  derived USD impact is unavailable without base-currency/FX provenance.

## Red-first evidence

- Gold fresh/upgrade initially exposed an init-readiness race; the canary now
  waits for the exact migration row and proves rollback/no registration.
- Replicon live first exposed an incorrect legacy fixture premise, then
  parameterized multi-statement setup, runtime DDL privilege, and view-owner RLS
  behavior. The corrected canary uses the exact legacy SHA, atomic CTE setup,
  migration-owned schemas, and a security-invoker current-head view.
- The focal gate initially failed only because its PostgreSQL parser-oracle was
  not running locally. With the exact CI service on port 55432 it passed cleanly.
- A first full live attempt was interrupted at 134 passed after the host filled
  with a recurring application-update download; its three setup errors were
  Docker log I/O errors. That run is excluded from final evidence.

## Validation

- Canonical focal Control Room: 9,166 passed; zero skips/failures/errors; one
  pandas future warning.
- PostgreSQL/RLS/Gold/Replicon live: 176 passed; zero
  skips/failures/errors.
- PDF/security contracts: 321 passed.
- Deterministic diagnostic fuzz: 83,125 cases checked; one harness test passed.
- Ruff 0.6.9 passed repository-wide. All 18 new Python files pass formatter
  check. Of 58 touched Python files, 49 are formatted and nine pre-existing
  files retain their frozen-base formatting debt; the broad advisory reports
  762 files would be reformatted and 834 already formatted. `compileall`, 70
  YAML parses, and `git diff --check` passed.
- Bandit 1.7.10 scanned 224,871 LOC. Its 288 medium-severity baseline findings
  are 220 low-confidence and 68 medium-confidence; the required high-confidence
  gate is clean. Pip-audit 2.7.3 covered all 17 requirement files with no known
  vulnerabilities. npm audited 498 console and seven E2E packages; both totals
  were zero at every severity.
- CI floors were updated only to the exact observed counts: 9,166 focal and 176
  live.

## Deliberately stopped dependencies

These items need a separately authorized phase. This branch does not edit or
depend on them.

- Migrations are code-only and were tested in disposable local databases; none
  was applied to production.
- Older Replicon P&L/consultant dataset definitions still contain historical
  `/20.0` and base-currency-as-USD assumptions. Correcting their public dataset
  schemas and frontend consumers requires a separate F1/data-contract phase.
  This branch prevents those rows from producing financial Control Room impact
  unless an explicit ready currency status is present.
- Derived Gold Monte Carlo still exposes the historical `confidence_band` name
  for scenario percentiles and needs a later public-contract/F1 change.
- Action templates are independent of analytical options. PR-B/execution was
  explicitly out of scope; no action is now derived from decorative options.

## Structural note

All new files are at most 300 lines; the largest new canary is exactly 300 and
the largest new runtime module is 292. Pre-existing files over 300 lines did not
grow relative to the frozen base:

- `console/app/routers/intelligence.py`: 1,159 -> 1,158 (-1)
- `console/app/services/control_room/api.py`: 5,934 -> 5,934 (0)
- `console/app/services/intelligence/calibration.py`: 642 -> 642 (0)
- `console/app/services/intelligence/decision_orchestrator.py`: 979 -> 970 (-9)
- `console/app/services/intelligence/monte_carlo.py`: 388 -> 387 (-1)
- `console/app/services/intelligence/orchestrator_execution.py`: 951 -> 950 (-1)
- `console/tests/test_control_room_service.py`: 3,843 -> 3,843 (0)
- `mcp-infra/app/tools/cartridges.py`: 1,457 -> 1,454 (-3)
- `mcp-infra/app/tools/control_room.py`: 1,185 -> 1,182 (-3)
- `mcp-infra/app/tools/postgres.py`: 314 -> 314 (0)
- `tests/test_decision_orchestrator.py`: 358 -> 333 (-25)
- `tests/test_decision_orchestrator_execution.py`: 540 -> 540 (0)
