-- Beta-8 — DEMO/DEV seed for the Control Room. NOT for production.
--
-- Why: the Control Room dashboard ITEMS/ALERTS/SOURCES are computed at
-- request time from gold datasets (via Refinement), so they need the
-- cartridge extraction pipeline to have run. But the Lessons and Threshold
-- panels read directly from DB tables — if those are empty the cockpit
-- looks half-broken in a demo. This seed deterministically populates those
-- DB-backed panels for the LOCAL dev workspace so a demo never shows empty
-- lessons/thresholds "by luck".
--
-- Scope: workspace-scoped + tenant-scoped (Default Tenant / Main Workspace).
-- Idempotent: thresholds via ON CONFLICT on the unique key; lessons via
-- WHERE NOT EXISTS (the table has no content unique index). Re-running adds
-- no duplicates. Rows are tagged metadata.demo = true so they are
-- identifiable and never confused with real operational data.
--
-- This file runs only from the dev-seed one-shot (infra/docker-compose.yml),
-- which is additionally guarded to skip when APP_ENV is production.

DO $$
DECLARE
    v_workspace_id UUID;
    v_tenant_id    UUID;
BEGIN
    SELECT w.id, t.id
      INTO v_workspace_id, v_tenant_id
      FROM workspaces w
      JOIN tenants t ON t.id = w.tenant_id
     WHERE w.name = 'Main Workspace'
       AND t.name = 'Default Tenant'
     LIMIT 1;

    IF v_workspace_id IS NULL THEN
        RAISE NOTICE '[demo-seed] Main Workspace/Default Tenant not found — skipping Control Room demo seed';
        RETURN;
    END IF;

    -- ── Threshold rule (drives the "Umbrales configurables" board) ──
    INSERT INTO control_room_thresholds (
        tenant_id, workspace_id, cartridge_id, anomaly_type, metric,
        warning_value, critical_value, currency, enabled, metadata
    )
    VALUES (
        v_tenant_id, v_workspace_id, 'replicon', 'low_margin', 'margen_bruto_pct',
        21, 12, 'USD', TRUE,
        '{"demo": true, "source": "demo_seed"}'::jsonb
    )
    ON CONFLICT (workspace_id, cartridge_id, anomaly_type, metric) DO UPDATE
        SET warning_value = EXCLUDED.warning_value,
            critical_value = EXCLUDED.critical_value,
            enabled = TRUE,
            updated_at = NOW();

    -- ── Learned lesson (drives the "Lecciones aprendidas" panel) ──
    INSERT INTO control_room_lessons (
        tenant_id, workspace_id, item_id, cartridge_id, anomaly_type,
        rule, confidence, metadata
    )
    SELECT
        v_tenant_id, v_workspace_id, 'demo-seed-low-margin', 'replicon', 'low_margin',
        'Si el margen bruto cae bajo el umbral, validar owner y evidencia antes de aprobar.',
        0.80, '{"demo": true, "source": "demo_seed"}'::jsonb
    WHERE NOT EXISTS (
        SELECT 1 FROM control_room_lessons
         WHERE workspace_id = v_workspace_id
           AND cartridge_id = 'replicon'
           AND anomaly_type = 'low_margin'
           AND rule = 'Si el margen bruto cae bajo el umbral, validar owner y evidencia antes de aprobar.'
    );

    RAISE NOTICE '[demo-seed] Control Room demo lessons/thresholds seeded for workspace %', v_workspace_id;
END $$;
