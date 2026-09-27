DO $entity_cron_timezone$
BEGIN
    IF to_regclass('public.entity_config') IS NULL THEN
        RETURN;
    END IF;
    ALTER TABLE entity_config
        ADD COLUMN IF NOT EXISTS cron_timezone TEXT NOT NULL DEFAULT 'UTC';
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
         WHERE conname = 'entity_config_cron_timezone_format_check'
           AND conrelid = 'public.entity_config'::regclass
    ) THEN
        ALTER TABLE entity_config
            ADD CONSTRAINT entity_config_cron_timezone_format_check
            CHECK (
                length(cron_timezone) BETWEEN 1 AND 64
                AND cron_timezone ~ '^[A-Za-z][A-Za-z0-9_+-]*(/[A-Za-z0-9_+-]+){0,2}$'
            ) NOT VALID;
    END IF;
    ALTER TABLE entity_config
        VALIDATE CONSTRAINT entity_config_cron_timezone_format_check;
END
$entity_cron_timezone$;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzzzw_entity_cron_timezone.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
