-- MODecissionsPaaS — System Settings (Fase 2A)
-- Configuración editable desde UI por admins.

CREATE TABLE IF NOT EXISTS system_settings (
    key          TEXT PRIMARY KEY,
    value        JSONB NOT NULL,
    is_secret    BOOLEAN NOT NULL DEFAULT FALSE,
    category     TEXT NOT NULL DEFAULT 'general',
    description  TEXT,
    updated_by   BIGINT,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_system_settings_category ON system_settings(category);

INSERT INTO system_settings (key, value, is_secret, category, description) VALUES
  ('feature_flags',
   '{"replicon":"ga","sap_successfactors":"alpha","sap_hcm":"alpha","sap_s4hana":"alpha"}'::jsonb,
   false, 'features',
   'Estado de cada cartucho: ga, beta, alpha.'),
  ('allowed_origins',
   '["http://localhost:8000","http://localhost:8001"]'::jsonb,
   false, 'security',
   'CORS origins permitidos.'),
  ('airflow_connection_mode',
   '"mock"'::jsonb,
   false, 'integrations',
   'mock = replicon-mock; real = credenciales productivas.'),
  ('replicon_base_url',
   '"https://na5.replicon.com/analytics"'::jsonb,
   false, 'integrations',
   'URL base del tenant Replicon.'),
  ('replicon_token',
   '""'::jsonb,
   true, 'integrations',
   'Bearer token del API de Replicon. Vacío en dev (usa mock).'),
  ('sap_successfactors_base_url',
   '""'::jsonb,
   false, 'integrations',
   'URL base de SAP SuccessFactors.'),
  ('sap_successfactors_client_id',
   '""'::jsonb,
   true, 'integrations',
   'OAuth2 client_id de SF.'),
  ('sap_successfactors_client_secret',
   '""'::jsonb,
   true, 'integrations',
   'OAuth2 client_secret de SF.')
ON CONFLICT (key) DO NOTHING;
