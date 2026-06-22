# Cartucho SuccessFactors Talent KB + WisdomBit

Version: 2026-06-22
Source: OMEGA-Cartucho-Talento-KB-WisdomBit.pdf
Scope: Talent layer over `sap_successfactors`

## Defaults

- Profile: `retail`
- Company profile: `femsa`
- Compensation: disabled
- SuccessFactors write-back: disabled
- PII and sensitive salary values: not exposed

## KnowledgeBits

| KB | Scope | Status in v1 |
| --- | --- | --- |
| KB-EMPLEADOS | Employee 360, hierarchy, headcount, basic movements | ready |
| KB-ROLES | Job code and derived role profiles | partial |
| KB-DESEMPENO | Performance, goals, calibration, potential | blocked until metadata confirms tenant entities |
| KB-COMPETENCIAS | Competencies and skills | blocked until metadata confirms tenant entities |
| KB-ASPIRACION | Declared career interests and aspiration signals | blocked until metadata confirms tenant entities |
| KB-APRENDIZAJE | Learning, compliance, certifications | enrichment only |
| KB-RECLUTAMIENTO | Requisitions and applications | enrichment only |
| KB-COSTOS | Cost and compensation sensitivity | future, disabled |

## WisdomBit WB-TALENTO

`WB-TALENTO` computes recommendations only. It never writes back to SuccessFactors and
does not trigger automatic actions.

- Fit Score components: competency `0.45`, performance `0.30`, aspiration `0.25`.
- Readiness: `Ready >= 80`, `Near >= 60`, `Not < 60`.
- Potential for 9-box: `0.6 competency + 0.4 aspiration`.
- 9-box cuts: low `< 3.0`, medium `>= 3.0`, high `>= 4.0`.
- Signals: `riesgo_salida`, `asignacion`, `pipeline`, `sesgo`, `sensibilidad`, `priorizacion`.

## Phase 1

Use only validated foundation data already available from AWS:

- Employees and active employment.
- Organization structure, manager hierarchy and headcount.
- Job codes and derived role coverage.
- Basic mobility from effective-dated `EmpJob` history.

## Phase 2 Blockers

These SuccessFactors entities must be validated through `$metadata` before extraction
or score calculation:

- `JobApplication`
- `PerformanceReview` / `FormHeader`
- `GoalPlan`
- Tenant-specific skills and competency entities
- `LearningItem`
- `Position`
- `FOCostCenter`
- `EmpEmploymentTermination`

## Control Room Contract

`GET /api/control-room/sap-successfactors/talent-kpis` returns:

- `profile`
- `readiness`
- `widgets`
- `signals`
- `blockers`
- `generated_at`

When C/P/A inputs are missing, datasets must return `insufficient_data` or
`partial/blocked` status instead of failing.
