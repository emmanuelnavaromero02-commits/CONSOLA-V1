-- Sprint v1.45 cúspide audit round 4 — concrete watchdogs.
--
-- The v1.45 PR ships ``copilot_watchdogs`` as an empty table; the
-- "Nivel 4 orchestrator" promise reads as decorative without at
-- least one real entry the goal_solver can match against. This
-- migration registers two specialists per cartridge that already
-- expose the underlying tools:
--
--   * ``margin_watchdog``    (Replicon) — billable / non-billable
--                              hours, project rate vs. cost, cartera
--                              vencida.
--   * ``capacity_watchdog``  (Replicon) — projected utilisation, PTO
--                              overlap, sprint capacity.
--   * ``headcount_watchdog`` (SAP HCM) — open positions, transfers,
--                              comp ratios.
--   * ``payroll_watchdog``   (SAP HCM) — ISR anomalies, retroactive
--                              adjustments, ledger mismatches.
--   * ``ar_watchdog``        (SAP S/4HANA) — cartera, credit limits.
--   * ``ap_watchdog``        (SAP S/4HANA) — supplier cost drift,
--                              missing invoices.
--   * ``user_lifecycle_watchdog`` (SAP SuccessFactors) — joiners,
--                              leavers, role changes.
--
-- Idempotent: ``ON CONFLICT (cartridge_id, slug) DO UPDATE`` keeps
-- the row's intent_keywords + tools in sync with the seed if an
-- operator re-runs this migration after a code update.

INSERT INTO copilot_watchdogs
    (cartridge_id, slug, name, description, intent_keywords, tools, risk_level)
VALUES
    -- ── Replicon ──────────────────────────────────────────────────
    ('replicon', 'margin_watchdog',
     'Margin Watchdog (Replicon)',
     'Detecta erosión de margen por horas no facturables, mix de '
     'roles caros vs. baratos y cartera vencida.',
     ARRAY['margen', 'margin', 'rentabilidad', 'profitability',
           'billable', 'facturable', 'no_facturable', 'cartera',
           'cobranza', 'overdue']::TEXT[],
     ARRAY['replicon.query_kb:project_financial_summary',
           'replicon.list_overdue_invoices']::TEXT[],
     'read'),
    ('replicon', 'capacity_watchdog',
     'Capacity Watchdog (Replicon)',
     'Calcula utilización proyectada, choques de PTO y capacidad '
     'sobreasignada en el horizonte próximo.',
     ARRAY['capacidad', 'capacity', 'utilizacion', 'utilization',
           'pto', 'vacaciones', 'sprint', 'allocation', 'asignacion']::TEXT[],
     ARRAY['replicon.list_users_by_utilization',
           'replicon.query_kb:capacity_horizon']::TEXT[],
     'read'),

    -- ── SAP HCM ───────────────────────────────────────────────────
    ('sap_hcm', 'headcount_watchdog',
     'Headcount Watchdog (SAP HCM)',
     'Vigila posiciones abiertas, transferencias inter-area y '
     'ratios de compensación fuera de banda.',
     ARRAY['headcount', 'plantilla', 'posicion', 'open_position',
           'transferencia', 'transfer', 'compensacion', 'comp_ratio']::TEXT[],
     ARRAY['sap_hcm.query_kb:headcount_snapshot',
           'sap_hcm.list_open_positions']::TEXT[],
     'read'),
    ('sap_hcm', 'payroll_watchdog',
     'Payroll Watchdog (SAP HCM)',
     'Detecta anomalías de ISR, retroactivos no procesados y '
     'mismatches contra el libro mayor.',
     ARRAY['nomina', 'payroll', 'isr', 'tax', 'retroactivo',
           'retroactive', 'ledger', 'libro_mayor']::TEXT[],
     ARRAY['sap_hcm.query_kb:payroll_anomalies',
           'sap_hcm.list_retroactive_runs']::TEXT[],
     'read'),

    -- ── SAP S/4HANA ───────────────────────────────────────────────
    ('sap_s4hana', 'ar_watchdog',
     'Accounts-Receivable Watchdog (SAP S/4HANA)',
     'Calcula cartera vencida por cliente, expone clientes al límite '
     'de crédito y candidatos a bloqueo de pedidos.',
     ARRAY['cartera', 'ar', 'accounts_receivable', 'credito',
           'credit_limit', 'overdue', 'vencido', 'cobranza',
           'bloqueo_pedidos', 'block_orders']::TEXT[],
     ARRAY['sap_s4hana.query_kb:ar_aging',
           'sap_s4hana.list_credit_limit_breach']::TEXT[],
     'read'),
    ('sap_s4hana', 'ap_watchdog',
     'Accounts-Payable Watchdog (SAP S/4HANA)',
     'Vigila drift de costos por proveedor y facturas pendientes de '
     'recepción.',
     ARRAY['ap', 'accounts_payable', 'proveedor', 'supplier',
           'costo', 'cost', 'invoice', 'factura', 'goods_receipt']::TEXT[],
     ARRAY['sap_s4hana.query_kb:ap_cost_drift',
           'sap_s4hana.list_missing_invoices']::TEXT[],
     'read'),

    -- ── SAP SuccessFactors ────────────────────────────────────────
    ('sap_successfactors', 'user_lifecycle_watchdog',
     'User Lifecycle Watchdog (SAP SuccessFactors)',
     'Joiners, leavers, cambios de manager y de organización.',
     ARRAY['lifecycle', 'joiners', 'leavers', 'altas', 'bajas',
           'manager', 'org_change', 'movement', 'movimiento']::TEXT[],
     ARRAY['sap_successfactors.query_kb:lifecycle_30d',
           'sap_successfactors.list_recent_terminations']::TEXT[],
     'read')
ON CONFLICT (cartridge_id, slug) DO UPDATE
    SET name            = EXCLUDED.name,
        description     = EXCLUDED.description,
        intent_keywords = EXCLUDED.intent_keywords,
        tools           = EXCLUDED.tools,
        risk_level      = EXCLUDED.risk_level;


-- Self-register so the migration tracker knows this file applied.
INSERT INTO schema_migrations (filename, applied_at)
VALUES ('94_copilot_watchdog_seed.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
