-- v1.45.x -- Ensure WB-TALENTO AgentOps monitor exists per SuccessFactors workspace.
--
-- Global seed agents are templates only. Scheduled AgentOps execution requires
-- tenant/workspace scope so runs, simulations and Control Room evidence persist
-- under the correct tenant isolation context.

WITH sf_workspaces AS (
    SELECT DISTINCT tenant_id, workspace_id
      FROM entity_config
     WHERE cartridge_id = 'sap_successfactors'
       AND COALESCE(enabled, TRUE) = TRUE
       AND tenant_id IS NOT NULL
       AND workspace_id IS NOT NULL
),
monitor_contract AS (
    SELECT
        jsonb_build_array(
            'mcp-infra__wisdom_bits__run',
            'mcp-infra__control_room__raise_analysis_alert',
            'mcp-infra__decision__orchestrate',
            'mcp-infra__simulation__monte_carlo_run',
            'mcp-infra__calibration__bayesian_state',
            'refinement__query_dataset',
            'refinement__get_schema'
        ) AS allowed_tools,
        jsonb_build_object(
            'cartridges', jsonb_build_array('sap_successfactors'),
            'kinds', jsonb_build_array('document', 'schema')
        ) AS rag_filter,
        jsonb_build_object(
            'role', 'monitor',
            'category', 'control_room',
            'scope', 'workspace',
            'variables', '{}'::jsonb,
            'schedule', jsonb_build_object(
                'enabled', TRUE,
                'cron', '*/15 * * * *',
                'tz', 'UTC',
                'prompt', 'Ejecuta el monitor WB-TALENTO con evidencia agregada y recommendation_only.'
            ),
            'monitor', jsonb_build_object(
                'engine', 'wisdom_bit',
                'wisdom_bit_id', 'WB-TALENTO',
                'dataset', 'sap_successfactors_talent_operational_features',
                'threshold', jsonb_build_object(
                    'status_not_in', jsonb_build_array('ready'),
                    'min_signal_count', 1,
                    'blockers_present', TRUE
                ),
                'severity', 'medium',
                'dedup_key', 'sap_successfactors:WB-TALENTO:workspace',
                'recommended_action', 'Revisar blockers C/P/A y priorizar acciones supervisadas en Control Room.',
                'recommendation_only', TRUE,
                'writeback_enabled', FALSE,
                'engines', jsonb_build_array(
                    jsonb_build_object(
                        'name', 'monte_carlo',
                        'enabled', TRUE,
                        'source_type', 'wisdom_bit',
                        'source_id', 'WB-TALENTO',
                        'horizon_days', 30,
                        'iterations', 1000,
                        'seed', 45120,
                        'model_version', 'wb-talento.monitor.v2',
                        'output_metric', 'delta',
                        'breach_threshold', -5,
                        'breach_direction', 'below',
                        'input_dataset', 'sap_successfactors_talent_simulation_inputs',
                        'input_variables_field', 'input_variables_json',
                        'assumptions_field', 'assumptions_json',
                        'evidence_refs_field', 'evidence_refs_json',
                        'status_field', 'input_status',
                        'ready_statuses', jsonb_build_array('ready'),
                        'assumptions', jsonb_build_object(
                            'basis', 'Agregado WB-TALENTO desde Gold operativo.',
                            'privacy', 'Sin full_name, user_id, PERNR, salario ni payCompValue.',
                            'decision_mode', 'recommendation_only'
                        ),
                        'evidence_refs', jsonb_build_array(jsonb_build_object('type', 'wisdom_bit', 'id', 'WB-TALENTO'))
                    ),
                    jsonb_build_object(
                        'name', 'bayesian_calibration',
                        'enabled', TRUE,
                        'calibration_group', 'sap_successfactors:talent_readiness',
                        'model_version', 'bayesian_calibration.v1',
                        'limit', 10,
                        'assumptions', jsonb_build_object(
                            'basis', 'Estado historico agregado de readiness de talento.',
                            'privacy', 'Sin full_name, user_id, PERNR, salario ni payCompValue.',
                            'decision_mode', 'recommendation_only'
                        ),
                        'evidence_refs', jsonb_build_array(jsonb_build_object('type', 'calibration_group', 'id', 'sap_successfactors:talent_readiness'))
                    ),
                    jsonb_build_object(
                        'name', 'decision_orchestrator',
                        'enabled', TRUE,
                        'source_type', 'wisdom_bit',
                        'source_id', 'WB-TALENTO',
                        'title', 'Decision operativa WB-TALENTO',
                        'description', 'Evaluar senales agregadas de talento con seguimiento supervisado.',
                        'time_horizon', '30d',
                        'metrics', jsonb_build_object(
                            'risk_metric', 'talent_readiness_delta',
                            'target', 'recommendation_only',
                            'privacy', 'aggregated'
                        ),
                        'constraints', jsonb_build_object(
                            'recommendation_only', TRUE,
                            'no_external_writeback', TRUE,
                            'no_pii', TRUE
                        ),
                        'evidence_refs', jsonb_build_array(jsonb_build_object('type', 'wisdom_bit', 'id', 'WB-TALENTO')),
                        'execute_engines', TRUE,
                        'engine_inputs', jsonb_build_object(
                            'monte_carlo', jsonb_build_object(
                                'source_type', 'wisdom_bit',
                                'source_id', 'WB-TALENTO',
                                'horizon_days', 30,
                                'iterations', 1000,
                                'seed', 45120,
                                'model_version', 'wb-talento.monitor.v2',
                                'input_dataset', 'sap_successfactors_talent_simulation_inputs',
                                'input_variables_field', 'input_variables_json',
                                'assumptions_field', 'assumptions_json',
                                'evidence_refs_field', 'evidence_refs_json',
                                'status_field', 'input_status',
                                'ready_statuses', jsonb_build_array('ready'),
                                'output_metric', 'delta',
                                'breach_threshold', -5,
                                'breach_direction', 'below',
                                'assumptions', jsonb_build_object(
                                    'basis', 'Agregado WB-TALENTO.',
                                    'privacy', 'Sin PII.',
                                    'decision_mode', 'recommendation_only'
                                ),
                                'evidence_refs', jsonb_build_array(jsonb_build_object('type', 'wisdom_bit', 'id', 'WB-TALENTO'))
                            ),
                            'bayesian_calibration', jsonb_build_object(
                                'calibration_group', 'sap_successfactors:talent_readiness',
                                'model_version', 'bayesian_calibration.v1',
                                'limit', 10
                            )
                        )
                    )
                )
            )
        ) AS extra
)
INSERT INTO agents (
    tenant_id, workspace_id, cartridge_id, slug, name, description,
    instructions, personality, allowed_tools, rag_filter, model,
    max_tokens, temperature, extra, is_active, updated_at
)
SELECT
    ws.tenant_id,
    ws.workspace_id,
    'sap_successfactors',
    'sap_successfactors_talent_monitor',
    'Talent AgentOps Monitor',
    'Monitor programado de WB-TALENTO con evidencia agregada y recommendation_only.',
    'Eres el monitor operativo de SuccessFactors Talent. Usa solo evidencia agregada, no expongas PII, no escribas en SuccessFactors y mantén todas las salidas en recommendation_only.',
    'Operativo, sobrio y auditable. Idioma del usuario.',
    contract.allowed_tools,
    contract.rag_filter,
    'claude-sonnet-4-6',
    2400,
    0.2,
    contract.extra,
    TRUE,
    NOW()
FROM sf_workspaces ws
CROSS JOIN monitor_contract contract
ON CONFLICT (workspace_id, cartridge_id, slug) WHERE workspace_id IS NOT NULL
DO UPDATE SET
    tenant_id = EXCLUDED.tenant_id,
    name = EXCLUDED.name,
    description = EXCLUDED.description,
    instructions = EXCLUDED.instructions,
    personality = EXCLUDED.personality,
    allowed_tools = EXCLUDED.allowed_tools,
    rag_filter = EXCLUDED.rag_filter,
    model = EXCLUDED.model,
    max_tokens = EXCLUDED.max_tokens,
    temperature = EXCLUDED.temperature,
    extra = EXCLUDED.extra,
    is_active = TRUE,
    updated_at = NOW();

INSERT INTO schema_migrations(filename, applied_at)
VALUES ('99zf_sap_successfactors_wb_talento_workspace_monitors.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
