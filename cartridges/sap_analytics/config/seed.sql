INSERT INTO cartridges (id, name, version, description, pattern, category, bronze_path)
VALUES (
    'sap_analytics', 'SAP Analytics', '1.0.0', 'SAP Analytics Orquestador', 'dag-based', 'cartridge', 'raw/sap_analytics/{entity}/load_date={date}/'
) ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name, description = EXCLUDED.description;

INSERT INTO cartridge_dags (cartridge_id, dag_id, file, description, trigger, params)
VALUES
    ('sap_analytics', 'sap_analytics_extract', 'sap_analytics_extract.py', 'Extrae', 'on-demand', '["entity","mode","from_date","to_date"]'),
    ('sap_analytics', 'sap_analytics_extract_all', 'sap_analytics_extract_all.py', 'Extrae', 'on-demand', '["mode","entities"]')
ON CONFLICT DO NOTHING;
