# AWS Beta Smoke Evidence

- status: `PASS`
- generated_at_utc: `2026-06-23T02:30:42.595386+00:00`
- ssm_command_id: `468d85fc-48fe-4099-b06f-b0f3a9119658`
- instance_id: `i-07a82861245b34481`
- region: `us-east-1`
- deploy_ref: `v1.45.116-beta`
- image_tag: `v1.45.116-beta`
- public_url: `https://console.7businesssolutions.com`

| Layer | Check | Status | Evidence |
|---|---|---|---|
| public-alb | public /healthz | PASS | status=200 version=1.45.116-beta expected=1.45.116-beta |
| public-alb | public /readyz | PASS | status=200 body={"ok":true,"service":"console"} |
| public-alb | public /readyz?require_data=1 | PASS | status=200 body={"ok":true,"service":"console"} |
| internal-ec2 | SSM command completed | PASS | command_id=468d85fc-48fe-4099-b06f-b0f3a9119658 response_code=0 |
| internal-ec2 | host VERSION file | PASS | path=/opt/modecissions/VERSION version=1.45.116-beta expected=1.45.116-beta |
| internal-ec2 | DEPLOY_REF matches expected | PASS | DEPLOY_REF=v1.45.116-beta expected=v1.45.116-beta |
| internal-ec2 | IMAGE_TAG matches expected | PASS | IMAGE_TAG=v1.45.116-beta expected=v1.45.116-beta |
| internal-ec2 | APP_ENV captured | PASS | APP_ENV=production |
| internal-ec2 | remote git ref captured | PASS | head=7306817e describe=v1.45.116-beta |
| internal-ec2 | AWS compose config validates | PASS | docker compose config --quiet returned 0 |
| internal-ec2 | console container running | PASS | running_services=hubspot,replicon,salesforce,sap-hcm,sap-s4hana,airflow,airflow-scheduler,console,mailhog,mcp-infra,postgres,postgres_gold,redis,refinement,sap-successfactors,superset,vault,workspace |
| internal-ec2 | internal /healthz | PASS | status=200 version=1.45.116-beta expected=1.45.116-beta |
| internal-ec2 | internal /readyz | PASS | status=200 |
| internal-ec2 | internal /readyz require_data | PASS | status=200 |
| internal-ec2 | internal /readyz require_intelligence | PASS | status=200 |
| internal-ec2 | internal superset health | PASS | status=200 |
| internal-ec2 | Replicon Gold lineage | PASS | entries=162 rows=6740 |
| internal-ec2 | Gold table gold_consultor_mensual has rows | PASS | rows=3060 |
| internal-ec2 | Gold table gold_pnl_mensual has rows | PASS | rows=1680 |
| internal-ec2 | Gold table gold_forecast_mensual has rows | PASS | rows=158 |
| internal-ec2 | HubSpot optional golden path | PASS | OMEGA_BETA_REQUIRE_HUBSPOT=0; AWS beta blocks on Replicon Gold |
| internal-ec2 | Gold FORCE RLS | PASS | weak_gold_tables=0 |
| internal-ec2 | Gold read role NOBYPASSRLS | PASS | role=omega_refinement_gold rolbypassrls=false |
| internal-ec2 | Replicon Intelligence signals | PASS | rows=39 |
| internal-ec2 | Replicon Control Room items | PASS | rows=53 |
| internal-ec2 | external write-back disabled | PASS | CONTROL_ROOM_ENABLE_EXTERNAL_WRITEBACK=<unset> |
