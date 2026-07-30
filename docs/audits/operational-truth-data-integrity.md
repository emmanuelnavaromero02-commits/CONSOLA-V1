# Operational truth: data integrity

Base: `49f792eedcf1177d8e7681478c5efecdc43e908b`

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
  rates and positive finite FX. Missing FX is `missing_fx`; other incomplete
  inputs are `insufficient_data`.
- `manual_fixture` is available only when `APP_ENV` is explicitly `test`,
  `local`, or `development`, including the engine executor's preflight.
  Standalone feature flags cannot enable it.
- Every Monte Carlo input distribution is persisted as a scenario assumption,
  not as an observation. Manual fixtures are additionally `not_calibrated`.

## Red-first evidence

- Manual fixtures: 29 expected failures before the guard; 33 canaries passed
  after the fix.
- Statistical fallbacks: 17 expected failures before the first fix and 5 more
  expected failures before the identity/selection hardening.
- Benchmark/FX: 5 expected failures before the fix and one expected alias
  failure during integration.
- Packaged seed: the live integrity gate rejected the changed SQL catalog until
  its reproducible 229-file digest was updated; the focal live test then passed.

## Validation

- Integrated focal: 237 passed; zero skips, failures, or errors.
- Final directly touched canaries: 122 passed; zero skips, failures, or errors.
- Manual/calibration/Monte Carlo executor follow-up: 67 passed.
- Replicon YAML/config: 29 passed.
- Tenant/workspace KB scope: 14 passed.
- Replicon WIP SQL: both statements parse with the DuckDB dialect.
- Control Room: 8,998 passed, versus the unchanged shared floor of 8,987;
  zero skips, failures, or errors.
- PostgreSQL/RLS live: 171 passed; zero skips, failures, or errors.
- PDF/security contracts: 320 passed.
- Deterministic diagnostic fuzz: 83,125 cases checked; one harness test passed.
- Packaged seed unit/focal follow-up: 12 unit tests and one live test passed.
- Ruff check passed repository-wide; Ruff format check passed for all touched
  Python files. `compileall`, 70 YAML parses, and `git diff --check` passed.
- Bandit reported no medium/high findings. Pip-audit covered 17 requirement
  files with no known vulnerabilities. Both npm audits reported zero
  vulnerabilities.
- Shared CI floors and workflows were not changed.

## Deliberately stopped dependencies

These items need a separately authorized phase. This branch does not edit or
depend on them.

- Applied `infra/init*` and Gold repair SQL still contain historical benchmark
  activation text. Existing materializations need an authorized repair; this
  branch does not apply migrations.
- `refinement/app/successfactors_fallbacks.py` contains a duplicate fallback.
  Refinement is owned by C1 and was not touched.
- Existing Replicon `kb_config` rows are seeded with `ON CONFLICT DO NOTHING`.
  Deployed rows need an authorized refresh/upsert before rollout.
- Preserving arbitrary third-currency invoice groups requires an additive data
  contract. This branch preserves USD/MXN and fails closed for other currencies.
- Derived Gold Monte Carlo still exposes the historical `confidence_band` name
  for scenario percentiles and needs a later public-contract/F1 change.
- Action templates are independent of analytical options. PR-B/execution was
  explicitly out of scope; no action is now derived from decorative options.

## Structural note

All new files and purpose-built runtime modules are at most 300 lines. Several
pre-existing modules already exceeded that limit at the frozen base:
`calibration_service.py`, `decision_orchestrator.py`,
`orchestrator_execution.py`, `seed_packaged_datasets.py`, and three existing
test modules. Changes there are limited to guards, the required catalog digest,
or canaries (plus deterministic Ruff formatting). Splitting those inherited
modules would remake shared runtime outside this mission.
