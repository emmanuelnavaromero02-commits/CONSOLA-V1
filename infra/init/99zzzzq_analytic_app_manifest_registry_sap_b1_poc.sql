-- GENERATED FILE — do not edit by hand.
--
-- Produced by scripts/generate_app_manifest_registry.py from the reviewed
-- manifests at cartridges/<cartridge>/apps/<app>.json and their HTML. Run the
-- generator to regenerate; CI runs it with --check and fails on any drift.
--
-- This is the database's copy of "what was reviewed". The reconciliation
-- function resolves the cartridge, the digest and the dataset list from here,
-- so a caller cannot supply any of them. Nothing in this file comes from
-- analytic_apps.datasets_used, from runtime metadata, or from scraping HTML.
--
-- The tables are created by 99zzt; this file only carries their contents.
--
-- Loaded as an upsert rather than a truncate: grants reference these rows with
-- ON DELETE RESTRICT, so wiping the table would either fail or, worse, need a
-- CASCADE that silently deleted live grants. Rows that leave the packaged set
-- are marked superseded and their dataset rows dropped only when nothing
-- references them, so history stays intact and authority still narrows.


INSERT INTO public.analytic_app_manifests
    (app_name, cartridge_id, manifest_digest, html_sha256, revision, source)
VALUES
    ('consultor_horas_costos', 'replicon', '24233010efb35bfd1f347c54b5e856ba162611c54917e8687125e9f3b110052b', '6d43faa79996e32ec8941a0362c51b17843e0e3570236d75b859366ffcb32b6a', 'active', 'packaged_manifest'),
    ('match_consultor_por_skill', 'replicon', 'd94e577700482f94f1b32c52c3613ef6079f99d70be6fe3f005567f79a530736', 'e29447249a461a491269e69dd213039d3f999a0df3bd9e15e18d84d091e6d7cc', 'active', 'packaged_manifest'),
    ('pipeline_forecast_dashboard', 'hubspot', '338fde860a92d006d213333ca6603106e888bc7f44a83f79041f9b5867309976', 'b242b57fe1e238cdd7e35d4332599f5bdbbbeb0adf0f081c1c372671150487fe', 'active', 'packaged_manifest'),
    ('pnl_revenue_manager', 'replicon', '960983518599d25a2aee4dd2c9a4045f3e226aecea09a66112232e1952250d4f', 'c1e43343229a55a37a8ffe037b81f860b46c91a1e86ff92cdec082301a329932', 'active', 'packaged_manifest'),
    ('resource_availability', 'replicon', '949f8e6537695783c145e51d2fc78364aff8196ec8777f8a2d62c75ae68df5b9', '90704bf398ce3dc2f5e81fffe08713a01a8af6c0a8a5ec3a333a84243e71eb13', 'active', 'packaged_manifest'),
    ('salesforce_cobertura_cuota', 'salesforce', '3732d3ebbc13dd2feac6b80d69533734724ee3a4769a96aa92a23abc148e80e4', 'f3209ac80580f7c90220481d142d5d1698fc7c425b0f9074198796abdee49164', 'active', 'packaged_manifest'),
    ('salesforce_deals_en_riesgo', 'salesforce', '48e2e89dc0e17266e369636ab2053f7a9f1d0556ba11ccd920cde8d86cb41743', '07f36560942c4f9b72aa3491133bc2b535ba49f658ea2da6e216c7f199fe063f', 'active', 'packaged_manifest'),
    ('salesforce_forecast_vs_capacidad', 'salesforce', '72a889e9e6b06bbf3c245cd227b5da43ec115e43501b3b764adfff01ae058d2e', 'a4b379e217501d29c068d1c50ffc7b96632214b2fc78294ebdc24b2ef1ae6fdb', 'active', 'packaged_manifest'),
    ('salesforce_pipeline_forecast', 'salesforce', '25316719f22b2703ff267603802a4a95d7a0911784a5da8cb45d8b0d43585166', 'e36f556a700eede9f25492f8c9b23e40ce4f22e50da17564a3c4fe96a76f2f84', 'active', 'packaged_manifest'),
    ('salesforce_velocidad_pipeline', 'salesforce', 'dce1767669e0e44db2644ce136ad71cc8857b99609660baabfec3b4fa90f4ea7', 'f6d828655d88ddd324e18fd62ea219c6d8b10eedbe12de243268b3c82af8d793', 'active', 'packaged_manifest'),
    ('salesforce_vendedor_margen', 'salesforce', 'bafda8076bb503669469b5ecb71a518122c79c17e9cf888faf228f1ae30abfb7', '0b117bec061b37266c742e77087664c7bc2d4d0dbf5369ad1f6cca3f5cfa5bda', 'active', 'packaged_manifest'),
    ('sap_b1_abasto', 'sap_b1', 'b14a4c0ab1355b0ae6c122b6f2f14f40946118c31b97646297c3dab52b9e0c86', 'c65e8bff78014e0199a4b8469ffab34db6178e6b11f9303c6857cdfe8bf0422f', 'active', 'packaged_manifest'),
    ('sap_b1_margen', 'sap_b1', 'b3ffdbb72ebd28fe963bd66d8287e147072ad86a2ac81b2d5a8843a6bd365d08', 'a86147c7e68b089f06d9a476e38b0bb25e5e61b6ebfa3352f85e74d79130e84a', 'active', 'packaged_manifest'),
    ('sap_b1_sellout', 'sap_b1', 'a981adbe40806ab8b68c7c85efcde11771813bd180171f4d2b553719589b4253', '8b189b18c743c4f94165aea99bb5e544fe134356e449a1e6fc893470de6ee35e', 'active', 'packaged_manifest'),
    ('sap_hcm_headcount_dashboard', 'sap_hcm', '3223017bcc1c6b37235ab2e78b5ac06bedb994dd12b8340388088c1260dc692b', 'f87812b363937c8a753655c4ac7afd91ddd2317ab61aec3024ffc2706087293e', 'active', 'packaged_manifest'),
    ('sap_hcm_people_quality_dashboard', 'sap_hcm', 'b24be4fccb1e838922a38fbbfaaea17492b60a3ef6c4d4f477abcf1b1d7d7666', 'f8de52c9627e8835436bcdb59525611bfcd8e61f9dd494dd548ac6610d4cca9b', 'active', 'packaged_manifest'),
    ('sap_s4hana_finance_dashboard', 'sap_s4hana', 'badc112ba2cafcdc47d6b7775910d0f9783040ec91c020ffa5434f2356433bda', 'f9437f0cac46af8f205e7e02692315910d45f2b40fa68fa849760c46672cb2aa', 'active', 'packaged_manifest'),
    ('sap_s4hana_sales_overview', 'sap_s4hana', '7bead1ce879c55cd0f023802660346902dd5dfe1f3032d68ef84aae9febf4da5', '7b38b84785dae13402a9ea3d894b438c6682a583b4e45f2be490e9791950277f', 'active', 'packaged_manifest'),
    ('sap_successfactors_talent_health', 'sap_successfactors', 'e012eceb41d4ccf15d5899fbb9104adbcd834b39c69ffaf3ea7f0ab06731846d', 'db7cb734059e01d76a187211cc990fbe7d235df5f3fa7e4939151fad03945980', 'active', 'packaged_manifest'),
    ('sap_successfactors_workforce_overview', 'sap_successfactors', 'b5312f8886b0f77f02bb26b2ce3fccc880e1be4afff0c82d902bc2ea6057987c', '53781a06acb50a28e8a677267063a57bc59b0bdd77047107fb80fa969a454d9c', 'active', 'packaged_manifest'),
    ('skill_gaps_heatmap', 'replicon', '6911bf4ac4a05d6b4e0da169e230e87be4295ed12b6fcd607a0702637ae8ce7f', '6ff92a3389697c4627c49a6f32344081d74931f8197f678586ba33e243654155', 'active', 'packaged_manifest')
ON CONFLICT (app_name) DO UPDATE SET
    cartridge_id = EXCLUDED.cartridge_id,
    manifest_digest = EXCLUDED.manifest_digest,
    html_sha256 = EXCLUDED.html_sha256,
    revision = 'active',
    source = 'packaged_manifest',
    generated_at = clock_timestamp();

INSERT INTO public.analytic_app_manifest_datasets
    (app_name, manifest_digest, dataset_name)
VALUES
    ('consultor_horas_costos', '24233010efb35bfd1f347c54b5e856ba162611c54917e8687125e9f3b110052b', 'consultor_asignacion'),
    ('consultor_horas_costos', '24233010efb35bfd1f347c54b5e856ba162611c54917e8687125e9f3b110052b', 'consultor_mensual'),
    ('match_consultor_por_skill', 'd94e577700482f94f1b32c52c3613ef6079f99d70be6fe3f005567f79a530736', 'consultor_asignacion'),
    ('match_consultor_por_skill', 'd94e577700482f94f1b32c52c3613ef6079f99d70be6fe3f005567f79a530736', 'costo_consultor_mensual'),
    ('match_consultor_por_skill', 'd94e577700482f94f1b32c52c3613ef6079f99d70be6fe3f005567f79a530736', 'empleados_maestro'),
    ('match_consultor_por_skill', 'd94e577700482f94f1b32c52c3613ef6079f99d70be6fe3f005567f79a530736', 'fact_empleado_skills'),
    ('pipeline_forecast_dashboard', '338fde860a92d006d213333ca6603106e888bc7f44a83f79041f9b5867309976', 'forecast_mensual'),
    ('pnl_revenue_manager', '960983518599d25a2aee4dd2c9a4045f3e226aecea09a66112232e1952250d4f', 'pnl_detalle_consultor'),
    ('pnl_revenue_manager', '960983518599d25a2aee4dd2c9a4045f3e226aecea09a66112232e1952250d4f', 'pnl_mensual'),
    ('resource_availability', '949f8e6537695783c145e51d2fc78364aff8196ec8777f8a2d62c75ae68df5b9', 'consultor_asignacion'),
    ('salesforce_cobertura_cuota', '3732d3ebbc13dd2feac6b80d69533734724ee3a4769a96aa92a23abc148e80e4', 'salesforce_cobertura_cuota'),
    ('salesforce_deals_en_riesgo', '48e2e89dc0e17266e369636ab2053f7a9f1d0556ba11ccd920cde8d86cb41743', 'salesforce_deals_en_riesgo'),
    ('salesforce_forecast_vs_capacidad', '72a889e9e6b06bbf3c245cd227b5da43ec115e43501b3b764adfff01ae058d2e', 'salesforce_forecast_vs_capacidad'),
    ('salesforce_pipeline_forecast', '25316719f22b2703ff267603802a4a95d7a0911784a5da8cb45d8b0d43585166', 'salesforce_pipeline_forecast'),
    ('salesforce_velocidad_pipeline', 'dce1767669e0e44db2644ce136ad71cc8857b99609660baabfec3b4fa90f4ea7', 'salesforce_velocidad_pipeline'),
    ('salesforce_vendedor_margen', 'bafda8076bb503669469b5ecb71a518122c79c17e9cf888faf228f1ae30abfb7', 'salesforce_vendedor_margen'),
    ('sap_b1_abasto', 'b14a4c0ab1355b0ae6c122b6f2f14f40946118c31b97646297c3dab52b9e0c86', 'sap_b1_item_coverage'),
    ('sap_b1_abasto', 'b14a4c0ab1355b0ae6c122b6f2f14f40946118c31b97646297c3dab52b9e0c86', 'sap_b1_material_cost_variance'),
    ('sap_b1_abasto', 'b14a4c0ab1355b0ae6c122b6f2f14f40946118c31b97646297c3dab52b9e0c86', 'sap_b1_supplier_lead_time'),
    ('sap_b1_margen', 'b3ffdbb72ebd28fe963bd66d8287e147072ad86a2ac81b2d5a8843a6bd365d08', 'sap_b1_data_quality'),
    ('sap_b1_margen', 'b3ffdbb72ebd28fe963bd66d8287e147072ad86a2ac81b2d5a8843a6bd365d08', 'sap_b1_entity_model'),
    ('sap_b1_margen', 'b3ffdbb72ebd28fe963bd66d8287e147072ad86a2ac81b2d5a8843a6bd365d08', 'sap_b1_kpi_reconciliation'),
    ('sap_b1_margen', 'b3ffdbb72ebd28fe963bd66d8287e147072ad86a2ac81b2d5a8843a6bd365d08', 'sap_b1_margin_consolidated_month'),
    ('sap_b1_margen', 'b3ffdbb72ebd28fe963bd66d8287e147072ad86a2ac81b2d5a8843a6bd365d08', 'sap_b1_margin_detail_month'),
    ('sap_b1_margen', 'b3ffdbb72ebd28fe963bd66d8287e147072ad86a2ac81b2d5a8843a6bd365d08', 'sap_b1_margin_kpis_month'),
    ('sap_b1_margen', 'b3ffdbb72ebd28fe963bd66d8287e147072ad86a2ac81b2d5a8843a6bd365d08', 'sap_b1_margin_reconciliation_month'),
    ('sap_b1_sellout', 'a981adbe40806ab8b68c7c85efcde11771813bd180171f4d2b553719589b4253', 'sap_b1_batch_expiry'),
    ('sap_b1_sellout', 'a981adbe40806ab8b68c7c85efcde11771813bd180171f4d2b553719589b4253', 'sap_b1_distributor_scorecard_month'),
    ('sap_b1_sellout', 'a981adbe40806ab8b68c7c85efcde11771813bd180171f4d2b553719589b4253', 'sap_b1_sellin_sellout_month'),
    ('sap_b1_sellout', 'a981adbe40806ab8b68c7c85efcde11771813bd180171f4d2b553719589b4253', 'sap_b1_sellout_by_customer_month'),
    ('sap_hcm_headcount_dashboard', '3223017bcc1c6b37235ab2e78b5ac06bedb994dd12b8340388088c1260dc692b', 'headcount_by_costcenter'),
    ('sap_hcm_headcount_dashboard', '3223017bcc1c6b37235ab2e78b5ac06bedb994dd12b8340388088c1260dc692b', 'headcount_by_department'),
    ('sap_hcm_headcount_dashboard', '3223017bcc1c6b37235ab2e78b5ac06bedb994dd12b8340388088c1260dc692b', 'headcount_by_position_type'),
    ('sap_hcm_people_quality_dashboard', 'b24be4fccb1e838922a38fbbfaaea17492b60a3ef6c4d4f477abcf1b1d7d7666', 'absence_by_type_and_month'),
    ('sap_hcm_people_quality_dashboard', 'b24be4fccb1e838922a38fbbfaaea17492b60a3ef6c4d4f477abcf1b1d7d7666', 'employees_anomalies'),
    ('sap_hcm_people_quality_dashboard', 'b24be4fccb1e838922a38fbbfaaea17492b60a3ef6c4d4f477abcf1b1d7d7666', 'manager_hierarchy'),
    ('sap_s4hana_finance_dashboard', 'badc112ba2cafcdc47d6b7775910d0f9783040ec91c020ffa5434f2356433bda', 'gl_balance_by_account'),
    ('sap_s4hana_finance_dashboard', 'badc112ba2cafcdc47d6b7775910d0f9783040ec91c020ffa5434f2356433bda', 'overdue_billing'),
    ('sap_s4hana_finance_dashboard', 'badc112ba2cafcdc47d6b7775910d0f9783040ec91c020ffa5434f2356433bda', 'purchase_spend_by_supplier'),
    ('sap_s4hana_sales_overview', '7bead1ce879c55cd0f023802660346902dd5dfe1f3032d68ef84aae9febf4da5', 'business_partner_anomalies'),
    ('sap_s4hana_sales_overview', '7bead1ce879c55cd0f023802660346902dd5dfe1f3032d68ef84aae9febf4da5', 'open_sales_orders'),
    ('sap_s4hana_sales_overview', '7bead1ce879c55cd0f023802660346902dd5dfe1f3032d68ef84aae9febf4da5', 'revenue_by_customer'),
    ('sap_successfactors_talent_health', 'e012eceb41d4ccf15d5899fbb9104adbcd834b39c69ffaf3ea7f0ab06731846d', 'sap_successfactors_employees_anomalies'),
    ('sap_successfactors_talent_health', 'e012eceb41d4ccf15d5899fbb9104adbcd834b39c69ffaf3ea7f0ab06731846d', 'sap_successfactors_manager_hierarchy'),
    ('sap_successfactors_talent_health', 'e012eceb41d4ccf15d5899fbb9104adbcd834b39c69ffaf3ea7f0ab06731846d', 'sap_successfactors_recruitment_funnel'),
    ('sap_successfactors_workforce_overview', 'b5312f8886b0f77f02bb26b2ce3fccc880e1be4afff0c82d902bc2ea6057987c', 'sap_successfactors_headcount_by_company'),
    ('sap_successfactors_workforce_overview', 'b5312f8886b0f77f02bb26b2ce3fccc880e1be4afff0c82d902bc2ea6057987c', 'sap_successfactors_headcount_by_department'),
    ('sap_successfactors_workforce_overview', 'b5312f8886b0f77f02bb26b2ce3fccc880e1be4afff0c82d902bc2ea6057987c', 'sap_successfactors_headcount_by_location'),
    ('sap_successfactors_workforce_overview', 'b5312f8886b0f77f02bb26b2ce3fccc880e1be4afff0c82d902bc2ea6057987c', 'sap_successfactors_turnover_by_period'),
    ('skill_gaps_heatmap', '6911bf4ac4a05d6b4e0da169e230e87be4295ed12b6fcd607a0702637ae8ce7f', 'analytic_skill_gap_by_manager')
ON CONFLICT (app_name, manifest_digest, dataset_name) DO NOTHING;

-- Anything no longer packaged stops being active, and its dataset rows go only
-- when no grant still points at them.
UPDATE public.analytic_app_manifests m
   SET revision = 'superseded'
 WHERE m.revision = 'active'
   AND m.app_name NOT IN ('consultor_horas_costos',
        'match_consultor_por_skill',
        'pipeline_forecast_dashboard',
        'pnl_revenue_manager',
        'resource_availability',
        'salesforce_cobertura_cuota',
        'salesforce_deals_en_riesgo',
        'salesforce_forecast_vs_capacidad',
        'salesforce_pipeline_forecast',
        'salesforce_velocidad_pipeline',
        'salesforce_vendedor_margen',
        'sap_b1_abasto',
        'sap_b1_margen',
        'sap_b1_sellout',
        'sap_hcm_headcount_dashboard',
        'sap_hcm_people_quality_dashboard',
        'sap_s4hana_finance_dashboard',
        'sap_s4hana_sales_overview',
        'sap_successfactors_talent_health',
        'sap_successfactors_workforce_overview',
        'skill_gaps_heatmap');

DELETE FROM public.analytic_app_manifest_datasets d
 WHERE NOT EXISTS (
        SELECT 1 FROM public.analytic_app_manifests m
         WHERE m.app_name = d.app_name
           AND m.manifest_digest = d.manifest_digest
           AND m.revision = 'active')
   AND NOT EXISTS (
        SELECT 1 FROM public.analytic_app_dataset_grants g
         WHERE g.app_name = d.app_name
           AND g.manifest_digest = d.manifest_digest
           AND g.dataset_name = d.dataset_name);

-- Exactly the packaged set, no more and no less. A mismatch here means the
-- image and this file disagree, which must stop the migration rather than
-- quietly grant from a stale list.
DO $registry_count$
DECLARE
    app_rows BIGINT;
BEGIN
    SELECT count(*) INTO app_rows FROM public.analytic_app_manifests
     WHERE revision = 'active';
    IF app_rows <> 21 THEN
        RAISE EXCEPTION 'app manifest registry expected % rows, found %',
            21, app_rows;
    END IF;
END
$registry_count$;

