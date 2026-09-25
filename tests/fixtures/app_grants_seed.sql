INSERT INTO users(id) VALUES (1) ON CONFLICT DO NOTHING;
INSERT INTO cartridges(id) VALUES ('sap_successfactors'),('replicon') ON CONFLICT DO NOTHING;
INSERT INTO tenants(id,name) VALUES ('11111111-1111-1111-1111-111111111111','A'),('22222222-2222-2222-2222-222222222222','B') ON CONFLICT DO NOTHING;
INSERT INTO workspaces(id,tenant_id,name) VALUES
 ('aaaaaaaa-0000-0000-0000-000000000001','11111111-1111-1111-1111-111111111111','wsA'),
 ('aaaaaaaa-0000-0000-0000-000000000002','11111111-1111-1111-1111-111111111111','wsB'),
 ('bbbbbbbb-0000-0000-0000-000000000002','22222222-2222-2222-2222-222222222222','wsB') ON CONFLICT DO NOTHING;
INSERT INTO analytic_apps
  (name,title,html,cartridge_id,created_by_id,tenant_id,workspace_id,scope_status)
VALUES
 ('sap_successfactors_talent_health','TH','<html></html>','sap_successfactors',NULL,NULL,NULL,'platform_template'),
 ('custom_user_app','CU','<html></html>','sap_successfactors',1,
  '11111111-1111-1111-1111-111111111111',
  'aaaaaaaa-0000-0000-0000-000000000001','scoped')
ON CONFLICT DO NOTHING;
INSERT INTO datasets(name,layer,cartridge,tenant_id,workspace_id) VALUES
 ('sap_successfactors_employees_anomalies','gold','sap_successfactors','11111111-1111-1111-1111-111111111111','aaaaaaaa-0000-0000-0000-000000000001'),
 ('sap_successfactors_employees_anomalies','gold','sap_successfactors','11111111-1111-1111-1111-111111111111','aaaaaaaa-0000-0000-0000-000000000002'),
 ('sap_successfactors_employees_anomalies','gold','sap_successfactors','22222222-2222-2222-2222-222222222222','bbbbbbbb-0000-0000-0000-000000000002'),
 ('sensitive_same_workspace','gold','sap_successfactors','11111111-1111-1111-1111-111111111111','aaaaaaaa-0000-0000-0000-000000000001'),
 ('consultor_mensual','gold','replicon','11111111-1111-1111-1111-111111111111','aaaaaaaa-0000-0000-0000-000000000001')
 ON CONFLICT DO NOTHING;
INSERT INTO cartridge_installations(id,tenant_id,workspace_id,cartridge_id,status) VALUES
 ('i1','11111111-1111-1111-1111-111111111111','aaaaaaaa-0000-0000-0000-000000000001','sap_successfactors','ready'),
 ('i2','11111111-1111-1111-1111-111111111111','aaaaaaaa-0000-0000-0000-000000000002','sap_successfactors','ready'),
 ('i3','22222222-2222-2222-2222-222222222222','bbbbbbbb-0000-0000-0000-000000000002','sap_successfactors','ready')
 ON CONFLICT DO NOTHING;
