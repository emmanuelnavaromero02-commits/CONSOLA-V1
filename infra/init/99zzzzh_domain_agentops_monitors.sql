-- Mission 4: AgentOps monitors for Finance, Operations and Risk.
--
-- Until now Talent was the only domain with a scheduled monitor
-- (99t_sap_successfactors_talent_agentops_monitor.sql). This file gives the same
-- treatment to the three domains whose cap-free aggregates already exist, and it
-- follows that file's structure deliberately: the same active_scope /
-- fallback_scope / payload CTEs, the same UPDATE-then-INSERT idempotency, and the
-- same reason for existing separately from the cartridge agent seeds.
--
-- WHY THESE ARE NEW ROWS AND NOT THE CONVERSATIONAL AGENTS
--
-- Controller Financiero, Enlace Operativo and Centinela de Deals are GLOBAL
-- templates (workspace_id IS NULL, seeded by 86_ and 94_). A scheduled monitor
-- cannot be a global row: agent_runner has to mint a signed tenant/workspace
-- security_context before it can call MCP, sync_agentops_monitor_candidates skips
-- any agent missing either id, and omega_rls_workspace_matches() returns false for
-- a NULL workspace_id, so a scheduled SELECT would not even see the row. Talent
-- resolved this the same way: sap_successfactors_talent_monitor is a separate
-- workspace-scoped row alongside the global sap_successfactors_talent_advisor.
--
-- Each monitor therefore reuses its conversational counterpart's personality
-- verbatim so the voice does not change between the two surfaces, and its slug is
-- the counterpart's slug plus "_monitor" so the pairing is obvious.
--
-- The second statement gives the three conversational agents the Mission 2 domain
-- KPI read tool, which is what upgrades them from "refinement__query_dataset with
-- a row cap" to real aggregates. It only ever ADDS a tool; nothing is removed, and
-- slug, personality, instructions and model are untouched.
--
-- The extra/allowed_tools JSON below is generated from the Python contracts in
-- console/app/domains/agentops/{finance,operations,risk}_monitor.py, and
-- tests/test_domain_monitor_seed.py asserts the two stay byte-equal. Talent has
-- five SQL variants that drifted from its Python contract; this pair cannot.

WITH active_scope AS (
    SELECT DISTINCT
           ci.tenant_id,
           ci.workspace_id,
           ci.cartridge_id
      FROM cartridge_installations ci
      LEFT JOIN tenant_entitlements te
        ON te.tenant_id = ci.tenant_id
       AND te.workspace_id = ci.workspace_id
       AND te.cartridge_id = ci.cartridge_id
     WHERE ci.cartridge_id IN ('salesforce', 'sap_s4hana')
       AND ci.status = 'ready'
       AND COALESCE(te.status, 'active') = 'active'
),
default_workspace AS (
    SELECT t.id AS tenant_id, w.id AS workspace_id
      FROM tenants t
      JOIN workspaces w ON w.tenant_id = t.id
     WHERE t.name = 'Default Tenant'
       AND w.name = 'Main Workspace'
       AND NOT EXISTS (SELECT 1 FROM active_scope)
     ORDER BY w.created_at NULLS LAST, w.id
     LIMIT 1
),
fallback_scope AS (
    SELECT d.tenant_id, d.workspace_id, c.cartridge_id
      FROM default_workspace d
      CROSS JOIN (VALUES ('salesforce'), ('sap_s4hana')) AS c(cartridge_id)
),
scope AS (
    SELECT tenant_id, workspace_id, cartridge_id FROM active_scope
    UNION ALL
    SELECT tenant_id, workspace_id, cartridge_id FROM fallback_scope
),
payload AS (
    SELECT
        scope.tenant_id,
        scope.workspace_id,
        'sap_s4hana'::text AS cartridge_id,
        'sap_s4hana_controller_financiero_monitor'::text AS slug,
        'Controller Financiero Monitor'::text AS name,
        'Monitor programado de Finanzas: revisa margen por proyecto, costo de nomina por departamento y horas facturables, y publica evidencia advisory en Control Room.'::text AS description,
        $monitor_prompt$Eres el monitor programado de Finanzas para Control Room.

## Objetivo
Revisar, de forma segura y auditable, el estado agregado de Finanzas: horas facturables registradas, costo de nomina por departamento y margen por proyecto. No escribes en SAP, no apruebas nada y no expones datos de personas.

## Que vigilas
1. Margen por proyecto: proyectos con margen negativo o en deterioro sostenido.
2. Costo de nomina por departamento contra las horas ejecutadas del mes cerrado.
3. Horas facturables registradas: caidas respecto al periodo anterior.

## Cuando alertas
Alerta cuando el estado del dominio no es `ready` y hay al menos una senal concreta. Si el dominio esta `unavailable` NO alertas: no hay evidencia suficiente y una alerta sin datos es ruido.

## Que evidencia citas
Solo agregados: totales, conteos, porcentajes y el periodo. Nunca un nombre de persona, un userid ni un identificador de empleado. Di siempre sobre que ventana temporal hablas.

## Honestidad obligatoria
- El margen se calcula sobre importes base porque `pnl_mensual` no tiene moneda verificada; dilo cuando reportes margen.
- Las horas facturables son un proxy de horas validadas sin facturar, no el dato facturado real.
- Antes de reportar una limitacion de datos, consulta la memoria compartida: si otro agente ya registro ese hallazgo, citalo en lugar de reportarlo como nuevo.

## Regla de seguridad
Todas las salidas son recommendation_only. No hay write-back externo ni acciones destructivas.$monitor_prompt$::text AS instructions,
        'Riguroso y analitico, tono Controller. Cifras y riesgo arriba, evidencia abajo. Honesto sobre datos parciales. Idioma del usuario.'::text AS personality,
        '[
          "mcp-infra__control_room__finance_kpis_read",
          "mcp-infra__wisdom_bits__run",
          "mcp-infra__control_room__raise_analysis_alert",
          "mcp-infra__decision__orchestrate",
          "mcp-infra__simulation__monte_carlo_run",
          "mcp-infra__calibration__bayesian_state",
          "mcp-infra__market_context_read",
          "mcp-infra__control_room__agent_memory_read",
          "mcp-infra__control_room__agent_memory_write",
          "refinement__query_dataset",
          "refinement__get_schema"
]'::jsonb AS allowed_tools,
        '{"cartridges": ["sap_s4hana"], "kinds": ["document", "schema"]}'::jsonb AS rag_filter,
        -- Model is per row on purpose. 17_agents.sql calls these "Model /
        -- generation params (per-agent so cost+quality scale with role)", and
        -- nothing in code pins a monitor to a model: agent_runtime reads
        -- row["model"] and only falls back to 'claude-sonnet-4-6' when the column
        -- is empty. So upgrading one monitor later is an UPDATE of this column in
        -- a new migration, with no code change and no redeploy. These three ship
        -- on the system default ('claude-sonnet-4-6', the DDL default), NOT on a
        -- larger model, until cost and quality per agent are decided.
        'claude-sonnet-4-6'::text AS model,
        2400::int AS max_tokens,
        0.2::real AS temperature,
        '{
          "category": "control_room",
          "memory_subjects": [
                    "cost_center_budget",
                    "pnl_mensual.base_currency"
          ],
          "monitor": {
                    "dataset": "finance_kpis",
                    "dedup_key": "sap_s4hana:WB-FINANZAS:workspace",
                    "domain": "Finanzas",
                    "engine": "wisdom_bit",
                    "engines": [
                              {
                                        "assumptions": {
                                                  "basis": "Agregado WB-FINANZAS desde las vistas de dominio.",
                                                  "decision_mode": "recommendation_only",
                                                  "privacy": "Solo agregados: sin nombres, sin identificadores de persona."
                                        },
                                        "breach_direction": "below",
                                        "breach_threshold": -5,
                                        "enabled": false,
                                        "evidence_refs": [
                                                  {
                                                            "id": "WB-FINANZAS",
                                                            "type": "wisdom_bit"
                                                  }
                                        ],
                                        "horizon_days": 30,
                                        "iterations": 1000,
                                        "model_version": "wb_finanzas.monitor.v1",
                                        "name": "monte_carlo",
                                        "output_metric": "delta",
                                        "reason": "sin dataset de inputs de simulacion para este dominio: no hay distribuciones agregadas publicadas, ver docs/data_gaps.md",
                                        "seed": 45210,
                                        "source_id": "WB-FINANZAS",
                                        "source_type": "wisdom_bit"
                              },
                              {
                                        "assumptions": {
                                                  "basis": "Estado historico agregado de Finanzas.",
                                                  "decision_mode": "recommendation_only",
                                                  "privacy": "Solo agregados: sin nombres, sin identificadores de persona."
                                        },
                                        "calibration_group": "sap_s4hana:finance_margin",
                                        "enabled": false,
                                        "evidence_refs": [
                                                  {
                                                            "id": "sap_s4hana:finance_margin",
                                                            "type": "calibration_group"
                                                  }
                                        ],
                                        "limit": 10,
                                        "model_version": "bayesian_calibration.v1",
                                        "name": "bayesian_calibration",
                                        "reason": "sin historial de calibracion para este grupo todavia: se habilita cuando existan resultados registrados"
                              },
                              {
                                        "constraints": {
                                                  "no_external_writeback": true,
                                                  "no_pii": true,
                                                  "recommendation_only": true
                                        },
                                        "description": "Evaluar si las senales agregadas de margen y costo requieren abrir investigacion con seguimiento supervisado.",
                                        "enabled": false,
                                        "engine_inputs": {},
                                        "evidence_refs": [
                                                  {
                                                            "id": "WB-FINANZAS",
                                                            "type": "wisdom_bit"
                                                  }
                                        ],
                                        "execute_engines": false,
                                        "metrics": {
                                                  "privacy": "aggregated",
                                                  "risk_metric": "project_margin_delta",
                                                  "target": "recommendation_only"
                                        },
                                        "name": "decision_orchestrator",
                                        "reason": "la procedencia de wisdom bit solo esta habilitada para el monitor de Talento: se habilita cuando este dominio tenga alertas de wisdom bit ya publicadas y su id sea reconocido como procedencia durable",
                                        "source_id": "WB-FINANZAS",
                                        "source_type": "wisdom_bit",
                                        "time_horizon": "30d",
                                        "title": "Decision operativa WB-FINANZAS"
                              }
                    ],
                    "recommendation_only": true,
                    "recommended_action": "Revisar proyectos con margen negativo y el costo de nomina del mes cerrado antes de comprometer nueva capacidad.",
                    "severity": "medium",
                    "threshold": {
                              "blockers_present": true,
                              "min_signal_count": 1,
                              "status_not_in": [
                                        "ready"
                              ]
                    },
                    "wisdom_bit_id": "WB-FINANZAS",
                    "writeback_enabled": false
          },
          "role": "monitor",
          "schedule": {
                    "cron": "2,17,32,47 * * * *",
                    "enabled": true,
                    "prompt": "Ejecuta el monitor WB-FINANZAS: lee los KPI agregados del dominio, revisa la memoria compartida antes de reportar, evalua blockers y senales, y publica una alerta advisory solo con evidencia agregada y recommendation_only.",
                    "tz": "UTC"
          },
          "scope": "workspace",
          "variables": {}
}'::jsonb AS extra
      FROM scope
     WHERE scope.cartridge_id = 'sap_s4hana'
    UNION ALL
    SELECT
        scope.tenant_id,
        scope.workspace_id,
        'salesforce'::text AS cartridge_id,
        'salesforce_ops_liaison_monitor'::text AS slug,
        'Enlace Operativo Monitor'::text AS name,
        'Monitor programado de Operacion: revisa corridas fallidas, frescura de datos por cartucho y ausentismo agregado, y publica evidencia advisory en Control Room.'::text AS description,
        $monitor_prompt$Eres el monitor programado de Operacion para Control Room.

## Objetivo
Revisar, de forma segura y auditable, la salud operativa agregada: corridas de pipeline, frescura de datos por cartucho y tasa de ausentismo a nivel empresa. No ejecutas pipelines, no apruebas nada y no expones datos de personas.

## Que vigilas
1. Cartuchos con corridas fallidas en las ultimas 24 horas y su tasa de fallo a 7 dias.
2. Cartuchos que llevan mas horas sin una extraccion exitosa que el umbral configurado.
3. Tasa de ausentismo del ultimo mes cerrado por tipo de ausencia.

## Cuando alertas
Alerta cuando el estado del dominio no es `ready` y hay al menos una senal concreta. Si el dominio esta `unavailable` NO alertas: sin bitacoras visibles no hay evidencia, y una alerta sin datos es ruido.

## Que evidencia citas
Solo agregados: conteos por cartucho y entidad, horas transcurridas, porcentajes y el periodo. Nunca el texto de un error de corrida, nunca un identificador de empleado.

## Honestidad obligatoria
- El umbral de frescura es un parametro, NO un SLA de negocio acordado: OMEGA no tiene uno configurado. Dilo cuando reportes incumplimiento.
- Solo SuccessFactors espeja `extraction_runs` en `pipeline_runs` y Replicon escribe `pipeline_runs` directamente; los demas cartuchos registran solo en `extraction_runs`. Un cartucho sin ninguna corrida exitosa visible no aparece en la lista.
- El ausentismo es a nivel EMPRESA, no por unidad organizativa, y el headcount es el snapshot actual, no el del mes analizado.
- Antes de reportar una limitacion de datos, consulta la memoria compartida: si otro agente ya registro ese hallazgo, citalo en lugar de reportarlo como nuevo.

## Regla de seguridad
Todas las salidas son recommendation_only. No hay write-back externo ni acciones destructivas.$monitor_prompt$::text AS instructions,
        'Puente entre ventas y operaciones. Alerta clara de meses en sobrecarga con magnitud. Idioma del usuario.'::text AS personality,
        '[
          "mcp-infra__control_room__operations_kpis_read",
          "mcp-infra__wisdom_bits__run",
          "mcp-infra__control_room__raise_analysis_alert",
          "mcp-infra__decision__orchestrate",
          "mcp-infra__simulation__monte_carlo_run",
          "mcp-infra__calibration__bayesian_state",
          "mcp-infra__control_room__agent_memory_read",
          "mcp-infra__control_room__agent_memory_write",
          "refinement__query_dataset",
          "refinement__get_schema"
]'::jsonb AS allowed_tools,
        '{"cartridges": ["salesforce", "replicon"], "kinds": ["document", "schema"]}'::jsonb AS rag_filter,
        -- Model is per row on purpose. 17_agents.sql calls these "Model /
        -- generation params (per-agent so cost+quality scale with role)", and
        -- nothing in code pins a monitor to a model: agent_runtime reads
        -- row["model"] and only falls back to 'claude-sonnet-4-6' when the column
        -- is empty. So upgrading one monitor later is an UPDATE of this column in
        -- a new migration, with no code change and no redeploy. These three ship
        -- on the system default ('claude-sonnet-4-6', the DDL default), NOT on a
        -- larger model, until cost and quality per agent are decided.
        'claude-sonnet-4-6'::text AS model,
        2400::int AS max_tokens,
        0.2::real AS temperature,
        '{
          "category": "control_room",
          "memory_subjects": [
                    "operations_freshness_sla",
                    "absence_by_type_and_month.org_unit",
                    "extraction_runs.salesforce_scope"
          ],
          "monitor": {
                    "dataset": "operations_kpis",
                    "dedup_key": "salesforce:WB-OPERACION:workspace",
                    "domain": "Operacion",
                    "engine": "wisdom_bit",
                    "engines": [
                              {
                                        "assumptions": {
                                                  "basis": "Agregado WB-OPERACION desde las vistas de dominio.",
                                                  "decision_mode": "recommendation_only",
                                                  "privacy": "Solo agregados: sin nombres, sin identificadores de persona."
                                        },
                                        "breach_direction": "below",
                                        "breach_threshold": -5,
                                        "enabled": false,
                                        "evidence_refs": [
                                                  {
                                                            "id": "WB-OPERACION",
                                                            "type": "wisdom_bit"
                                                  }
                                        ],
                                        "horizon_days": 30,
                                        "iterations": 1000,
                                        "model_version": "wb_operacion.monitor.v1",
                                        "name": "monte_carlo",
                                        "output_metric": "delta",
                                        "reason": "sin dataset de inputs de simulacion para este dominio: no hay distribuciones agregadas publicadas, ver docs/data_gaps.md",
                                        "seed": 45220,
                                        "source_id": "WB-OPERACION",
                                        "source_type": "wisdom_bit"
                              },
                              {
                                        "assumptions": {
                                                  "basis": "Estado historico agregado de Operacion.",
                                                  "decision_mode": "recommendation_only",
                                                  "privacy": "Solo agregados: sin nombres, sin identificadores de persona."
                                        },
                                        "calibration_group": "salesforce:operations_health",
                                        "enabled": false,
                                        "evidence_refs": [
                                                  {
                                                            "id": "salesforce:operations_health",
                                                            "type": "calibration_group"
                                                  }
                                        ],
                                        "limit": 10,
                                        "model_version": "bayesian_calibration.v1",
                                        "name": "bayesian_calibration",
                                        "reason": "sin historial de calibracion para este grupo todavia: se habilita cuando existan resultados registrados"
                              },
                              {
                                        "constraints": {
                                                  "no_external_writeback": true,
                                                  "no_pii": true,
                                                  "recommendation_only": true
                                        },
                                        "description": "Evaluar si las senales agregadas de fallos y frescura requieren intervencion con seguimiento supervisado.",
                                        "enabled": false,
                                        "engine_inputs": {},
                                        "evidence_refs": [
                                                  {
                                                            "id": "WB-OPERACION",
                                                            "type": "wisdom_bit"
                                                  }
                                        ],
                                        "execute_engines": false,
                                        "metrics": {
                                                  "privacy": "aggregated",
                                                  "risk_metric": "pipeline_failure_rate_delta",
                                                  "target": "recommendation_only"
                                        },
                                        "name": "decision_orchestrator",
                                        "reason": "la procedencia de wisdom bit solo esta habilitada para el monitor de Talento: se habilita cuando este dominio tenga alertas de wisdom bit ya publicadas y su id sea reconocido como procedencia durable",
                                        "source_id": "WB-OPERACION",
                                        "source_type": "wisdom_bit",
                                        "time_horizon": "30d",
                                        "title": "Decision operativa WB-OPERACION"
                              }
                    ],
                    "recommendation_only": true,
                    "recommended_action": "Revisar los cartuchos con fallos recientes y los que exceden el umbral de frescura antes de confiar en sus cifras.",
                    "severity": "medium",
                    "threshold": {
                              "blockers_present": true,
                              "min_signal_count": 1,
                              "status_not_in": [
                                        "ready"
                              ]
                    },
                    "wisdom_bit_id": "WB-OPERACION",
                    "writeback_enabled": false
          },
          "role": "monitor",
          "schedule": {
                    "cron": "7,22,37,52 * * * *",
                    "enabled": true,
                    "prompt": "Ejecuta el monitor WB-OPERACION: lee los KPI agregados del dominio, revisa la memoria compartida antes de reportar, evalua blockers y senales, y publica una alerta advisory solo con evidencia agregada y recommendation_only.",
                    "tz": "UTC"
          },
          "scope": "workspace",
          "variables": {}
}'::jsonb AS extra
      FROM scope
     WHERE scope.cartridge_id = 'salesforce'
    UNION ALL
    SELECT
        scope.tenant_id,
        scope.workspace_id,
        'salesforce'::text AS cartridge_id,
        'salesforce_deal_risk_sentinel_monitor'::text AS slug,
        'Centinela de Deals Monitor'::text AS name,
        'Monitor programado de Riesgo: revisa deals vencidos, poblacion en riesgo de rotacion y fin de contrato proximo, y publica evidencia advisory en Control Room.'::text AS description,
        $monitor_prompt$Eres el monitor programado de Riesgo para Control Room.

## Objetivo
Revisar, de forma segura y auditable, el riesgo agregado: poblacion en riesgo de rotacion, fin de contrato proximo y deals que se estan cayendo. No escribes en Salesforce ni en SuccessFactors, no apruebas nada y no expones datos de personas.

## Que vigilas
1. Deals abiertos con cierre vencido y su monto agregado por etapa y por motivo.
2. Poblacion en banda de riesgo alto de rotacion.
3. Empleados con fin de contrato dentro de 30, 60 y 90 dias.

## Cuando alertas
Alerta cuando el estado del dominio no es `ready` y hay al menos una senal concreta. Si el dominio esta `unavailable` NO alertas: sin evidencia no hay riesgo demostrable.

## Que evidencia citas
Solo agregados: conteos por banda, montos por etapa, dias vencidos maximos. NUNCA el vendedor de un deal, nunca un nombre de empleado, nunca un identificador de persona.

## Honestidad obligatoria
- La rotacion reutiliza las bandas de riesgo de Talento; no la recalculas ni la mejoras.
- El fin de contrato excluye la fecha centinela de 2030, que no es un vencimiento real.
- El desglose por motivo de riesgo repite el mismo filtro de la consulta y no distingue causas independientes.
- `cost_center_overrun` no existe porque no hay presupuesto por centro de costo en ningun cartucho. Antes de reportarlo, consulta la memoria compartida: Finanzas ya registro ese hallazgo y debes citarlo en lugar de repetirlo como nuevo.

## Regla de seguridad
Todas las salidas son recommendation_only. No hay write-back externo ni acciones destructivas.$monitor_prompt$::text AS instructions,
        'Directo y proactivo. Lista priorizada por monto con dueño y motivo. Idioma del usuario.'::text AS personality,
        '[
          "mcp-infra__control_room__risk_kpis_read",
          "mcp-infra__wisdom_bits__run",
          "mcp-infra__control_room__raise_analysis_alert",
          "mcp-infra__decision__orchestrate",
          "mcp-infra__simulation__monte_carlo_run",
          "mcp-infra__calibration__bayesian_state",
          "mcp-infra__market_context_read",
          "mcp-infra__control_room__agent_memory_read",
          "mcp-infra__control_room__agent_memory_write",
          "refinement__query_dataset",
          "refinement__get_schema"
]'::jsonb AS allowed_tools,
        '{"cartridges": ["salesforce"], "kinds": ["document", "schema"]}'::jsonb AS rag_filter,
        -- Model is per row on purpose. 17_agents.sql calls these "Model /
        -- generation params (per-agent so cost+quality scale with role)", and
        -- nothing in code pins a monitor to a model: agent_runtime reads
        -- row["model"] and only falls back to 'claude-sonnet-4-6' when the column
        -- is empty. So upgrading one monitor later is an UPDATE of this column in
        -- a new migration, with no code change and no redeploy. These three ship
        -- on the system default ('claude-sonnet-4-6', the DDL default), NOT on a
        -- larger model, until cost and quality per agent are decided.
        'claude-sonnet-4-6'::text AS model,
        2400::int AS max_tokens,
        0.2::real AS temperature,
        '{
          "category": "control_room",
          "memory_subjects": [
                    "cost_center_budget",
                    "salesforce_deals_en_riesgo.motivo_riesgo",
                    "employment_end_sentinel_date"
          ],
          "monitor": {
                    "dataset": "risk_kpis",
                    "dedup_key": "salesforce:WB-DEALS:workspace",
                    "domain": "Riesgo",
                    "engine": "wisdom_bit",
                    "engines": [
                              {
                                        "assumptions": {
                                                  "basis": "Agregado WB-DEALS desde las vistas de dominio.",
                                                  "decision_mode": "recommendation_only",
                                                  "privacy": "Solo agregados: sin nombres, sin identificadores de persona."
                                        },
                                        "breach_direction": "below",
                                        "breach_threshold": -5,
                                        "enabled": false,
                                        "evidence_refs": [
                                                  {
                                                            "id": "WB-DEALS",
                                                            "type": "wisdom_bit"
                                                  }
                                        ],
                                        "horizon_days": 30,
                                        "iterations": 1000,
                                        "model_version": "wb_deals.monitor.v1",
                                        "name": "monte_carlo",
                                        "output_metric": "delta",
                                        "reason": "sin dataset de inputs de simulacion para este dominio: no hay distribuciones agregadas publicadas, ver docs/data_gaps.md",
                                        "seed": 45230,
                                        "source_id": "WB-DEALS",
                                        "source_type": "wisdom_bit"
                              },
                              {
                                        "assumptions": {
                                                  "basis": "Estado historico agregado de Riesgo.",
                                                  "decision_mode": "recommendation_only",
                                                  "privacy": "Solo agregados: sin nombres, sin identificadores de persona."
                                        },
                                        "calibration_group": "salesforce:deal_slippage",
                                        "enabled": false,
                                        "evidence_refs": [
                                                  {
                                                            "id": "salesforce:deal_slippage",
                                                            "type": "calibration_group"
                                                  }
                                        ],
                                        "limit": 10,
                                        "model_version": "bayesian_calibration.v1",
                                        "name": "bayesian_calibration",
                                        "reason": "sin historial de calibracion para este grupo todavia: se habilita cuando existan resultados registrados"
                              },
                              {
                                        "constraints": {
                                                  "no_external_writeback": true,
                                                  "no_pii": true,
                                                  "recommendation_only": true
                                        },
                                        "description": "Evaluar si las senales agregadas de deals vencidos y rotacion requieren abrir investigacion con seguimiento supervisado.",
                                        "enabled": false,
                                        "engine_inputs": {},
                                        "evidence_refs": [
                                                  {
                                                            "id": "WB-DEALS",
                                                            "type": "wisdom_bit"
                                                  }
                                        ],
                                        "execute_engines": false,
                                        "metrics": {
                                                  "privacy": "aggregated",
                                                  "risk_metric": "deal_slippage_delta",
                                                  "target": "recommendation_only"
                                        },
                                        "name": "decision_orchestrator",
                                        "reason": "la procedencia de wisdom bit solo esta habilitada para el monitor de Talento: se habilita cuando este dominio tenga alertas de wisdom bit ya publicadas y su id sea reconocido como procedencia durable",
                                        "source_id": "WB-DEALS",
                                        "source_type": "wisdom_bit",
                                        "time_horizon": "30d",
                                        "title": "Decision operativa WB-DEALS"
                              }
                    ],
                    "recommendation_only": true,
                    "recommended_action": "Revisar los deals con cierre vencido por monto y los vencimientos de contrato dentro de 30 dias antes del siguiente ciclo.",
                    "severity": "medium",
                    "threshold": {
                              "blockers_present": true,
                              "min_signal_count": 1,
                              "status_not_in": [
                                        "ready"
                              ]
                    },
                    "wisdom_bit_id": "WB-DEALS",
                    "writeback_enabled": false
          },
          "role": "monitor",
          "schedule": {
                    "cron": "12,27,42,57 * * * *",
                    "enabled": true,
                    "prompt": "Ejecuta el monitor WB-DEALS: lee los KPI agregados del dominio, revisa la memoria compartida antes de reportar, evalua blockers y senales, y publica una alerta advisory solo con evidencia agregada y recommendation_only.",
                    "tz": "UTC"
          },
          "scope": "workspace",
          "variables": {}
}'::jsonb AS extra
      FROM scope
     WHERE scope.cartridge_id = 'salesforce'
),
updated AS (
    UPDATE agents a
       SET name = p.name,
           description = p.description,
           instructions = p.instructions,
           personality = p.personality,
           allowed_tools = p.allowed_tools,
           rag_filter = p.rag_filter,
           model = p.model,
           max_tokens = p.max_tokens,
           temperature = p.temperature,
           extra = p.extra,
           is_active = TRUE,
           updated_at = NOW()
      FROM payload p
     WHERE a.workspace_id = p.workspace_id
       AND a.cartridge_id = p.cartridge_id
       AND a.slug = p.slug
     RETURNING a.workspace_id, a.cartridge_id, a.slug
)
INSERT INTO agents (
    tenant_id, workspace_id, cartridge_id, slug, name, description,
    instructions, personality, allowed_tools, rag_filter, model,
    max_tokens, temperature, extra, is_active
)
SELECT
    p.tenant_id, p.workspace_id, p.cartridge_id, p.slug, p.name, p.description,
    p.instructions, p.personality, p.allowed_tools, p.rag_filter, p.model,
    p.max_tokens, p.temperature, p.extra, TRUE
FROM payload p
-- Correlated per row, unlike 99t's bare NOT EXISTS: with three agents in one
-- statement, an uncorrelated guard would suppress the INSERT of the other two as
-- soon as any one of them was updated.
WHERE NOT EXISTS (
    SELECT 1
      FROM updated u
     WHERE u.workspace_id = p.workspace_id
       AND u.cartridge_id = p.cartridge_id
       AND u.slug = p.slug
);

-- The conversational agents gain their domain's aggregate read. jsonb_insert is
-- avoided in favour of an explicit containment check so re-running is a no-op and
-- the existing tool order is preserved.
WITH kpi_tool(cartridge_id, slug, tool) AS (
    VALUES
    ('sap_s4hana', 'sap_s4hana_controller_financiero', 'mcp-infra__control_room__finance_kpis_read'),
    ('salesforce', 'salesforce_ops_liaison', 'mcp-infra__control_room__operations_kpis_read'),
    ('salesforce', 'salesforce_deal_risk_sentinel', 'mcp-infra__control_room__risk_kpis_read')
)
UPDATE agents a
   SET allowed_tools = a.allowed_tools || to_jsonb(k.tool),
       updated_at = NOW()
  FROM kpi_tool k
 WHERE a.cartridge_id = k.cartridge_id
   AND a.slug = k.slug
   AND NOT (a.allowed_tools @> to_jsonb(k.tool));

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzzzh_domain_agentops_monitors.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
