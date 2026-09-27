-- Control Room direct actions: action bindings may now carry the four audited
-- direct templates next to the follow-up task. Intents stay follow-up only.
ALTER TABLE control_room_action_tokens
    DROP CONSTRAINT IF EXISTS control_room_action_tokens_shape_chk;
ALTER TABLE control_room_action_tokens
    ADD CONSTRAINT control_room_action_tokens_shape_chk CHECK ((
      (
        stage = 'action_binding'
        AND binding_handle_nonce IS NOT NULL
        AND (octet_length(binding_handle_nonce) = 32) IS TRUE
        AND item_id IS NOT NULL AND template_id IS NOT NULL
        AND (template_id IN (
          'create_followup_task',
          'approve_exception',
          'open_in_studio',
          'create_decision_proposal',
          'reopen_exception'
        )) IS TRUE
        AND binding_digest IS NOT NULL AND evidence_digest IS NOT NULL
        AND observation_fingerprint IS NOT NULL AND contract_digest IS NOT NULL
        AND target_digest IS NOT NULL AND decision_digest IS NOT NULL
        AND access_revision_digest IS NOT NULL AND rbac_policy_digest IS NOT NULL
        AND NOT (
          intent_id IS NOT NULL AND binding_dry_run_action_run_id IS NOT NULL
        )
        AND (
          (binding_dry_run_action_run_id IS NULL
            AND binding_dry_run_evidence_digest IS NULL)
          OR (binding_dry_run_action_run_id IS NOT NULL
            AND binding_dry_run_evidence_digest IS NOT NULL)
        )
        AND (
          template_id = 'create_followup_task'
          OR (intent_id IS NULL AND binding_dry_run_action_run_id IS NULL)
        )
      ) OR (
        stage <> 'action_binding' AND binding_handle_nonce IS NULL
        AND intent_id IS NOT NULL
        AND item_id IS NULL AND template_id IS NULL
        AND binding_digest IS NULL AND evidence_digest IS NULL
        AND observation_fingerprint IS NULL AND contract_digest IS NULL
        AND target_digest IS NULL AND decision_digest IS NULL
        AND access_revision_digest IS NULL AND rbac_policy_digest IS NULL
        AND binding_dry_run_action_run_id IS NULL
        AND binding_dry_run_evidence_digest IS NULL
      )
    ) IS TRUE);

INSERT INTO control_room_action_templates
    (template_id, cartridge_id, anomaly_type, label, description, action_kind, risk_level, mode_default, requires_approval, config)
VALUES
    ('approve_exception', 'platform', NULL, 'Aprobar Excepción', 'Archiva el hallazgo como excepción aprobada con motivo auditado; es reversible.', 'exception_approval', 'low', 'direct', FALSE, '{"external_write": false}'::jsonb),
    ('open_in_studio', 'platform', NULL, 'Ajustar en Estudio', 'Abre la capa Oro de la fuente de datos del hallazgo en Estudio.', 'studio_adjustment', 'low', 'direct', FALSE, '{"external_write": false}'::jsonb),
    ('create_decision_proposal', 'platform', NULL, 'Crear Propuesta de Decisión', 'Crea una decisión con compromiso a 7 días que pasa al Consejo para aprobación.', 'decision_proposal', 'low', 'direct', FALSE, '{"external_write": false}'::jsonb),
    ('reopen_exception', 'platform', NULL, 'Reabrir hallazgo', 'Devuelve una excepción aprobada a los hallazgos abiertos con motivo auditado.', 'exception_reopen', 'low', 'direct', FALSE, '{"external_write": false}'::jsonb)
ON CONFLICT (template_id) DO UPDATE
SET cartridge_id = EXCLUDED.cartridge_id,
    label = EXCLUDED.label,
    description = EXCLUDED.description,
    action_kind = EXCLUDED.action_kind,
    risk_level = EXCLUDED.risk_level,
    mode_default = EXCLUDED.mode_default,
    requires_approval = EXCLUDED.requires_approval,
    config = EXCLUDED.config,
    updated_at = NOW();

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzzzv_control_room_direct_action_templates.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
