DO $blank_cron$
BEGIN
    IF to_regclass('public.entity_config') IS NULL THEN
        RETURN;
    END IF;
    UPDATE entity_config SET cron_expression = NULL WHERE cron_expression = '';
END
$blank_cron$;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzzzy_entity_config_blank_cron.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
