-- 99zc_sap_successfactors_talent_connection_guard.sql
--
-- 99m assigns the default FEMSA Vault connection before 99zb creates the extra
-- Talent entities. Keep those later entities extractable without restoring any
-- environment fallback in the Airflow DAGs.

WITH talent_entities(entity) AS (
    VALUES
        ('FOEventReason'),
        ('JobApplication'),
        ('PerformanceReview'),
        ('GoalPlan'),
        ('CompetencyEntity'),
        ('UserSkill'),
        ('SkillProfile'),
        ('CareerWorksheet'),
        ('CareerInterest'),
        ('SuccessionNomination'),
        ('LearningItem'),
        ('LearningAssignment'),
        ('LearningHistory')
)
UPDATE entity_config ec
   SET connection_id = 'femsa_sf',
       dag_id        = COALESCE(NULLIF(ec.dag_id, ''), 'sap_successfactors_extract'),
       trigger_type  = COALESCE(NULLIF(ec.trigger_type, ''), 'manual')
  FROM talent_entities te
 WHERE ec.cartridge_id = 'sap_successfactors'
   AND ec.entity = te.entity
   AND ec.enabled IS TRUE
   AND COALESCE(NULLIF(ec.connection_id, ''), '') = '';

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zc_sap_successfactors_talent_connection_guard.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
