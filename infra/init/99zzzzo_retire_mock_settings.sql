DO $retire_mock_settings$
BEGIN
    IF to_regclass('public.system_settings') IS NULL THEN
        RETURN;
    END IF;
    DELETE FROM system_settings WHERE key = 'airflow_connection_mode';
    UPDATE system_settings
       SET description = 'Bearer token del API de Replicon.'
     WHERE key = 'replicon_token';
END
$retire_mock_settings$;

INSERT INTO schema_migrations (filename, applied_at)
VALUES ('99zzzzo_retire_mock_settings.sql', NOW())
ON CONFLICT (filename) DO NOTHING;
