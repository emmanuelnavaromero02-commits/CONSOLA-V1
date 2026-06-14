# Cartuchos Config-Only + Runtime Genérico Aislado

Dirección de producto: los cartuchos deben ser configuración. La plataforma provee los servicios genéricos y aplica aislamiento por `tenant_id`/`workspace_id` con RLS, entitlements, contexto firmado y prefijos de storage scoped.

## Contrato

Cada cartucho declara configuración, no ownership de seguridad:

- `connector.yaml`: identidad, auth, API, storage, watermark, schema UI.
- `entities.yaml`: entidades, claves naturales, modos, watermarks, DAG legacy asociado durante transición.
- `knowledge_bits.yaml`: consultas permitidas y outputs.
- `intelligence.yaml`: datasets e indicadores esperados.

Los servicios genéricos de plataforma resuelven:

- schema y semantic
- watermarks
- jobs/logs
- preview
- lineage/catalog
- extracción
- RAG/vector
- Control Room/MCP

## Reglas De Aislamiento

- UI/LLM/MCP client no puede enviar `tenant_id`, `workspace_id` ni `security_context` como argumento de tool.
- Console inyecta el contexto firmado y el gateway MCP rechaza scope enviado por el cliente.
- Toda lectura técnica valida entitlements del workspace activo.
- Toda escritura operacional lleva `tenant_id` y `workspace_id`.
- Storage usa prefijos scoped:

```text
raw/<cartridge>/<entity>/tenant_id=<tenant_id>/workspace_id=<workspace_id>/...
silver/<cartridge>/<entity>/tenant_id=<tenant_id>/workspace_id=<workspace_id>/...
gold/<cartridge>/<entity>/tenant_id=<tenant_id>/workspace_id=<workspace_id>/...
```

## Migración Por Fases

1. UX/IAM seguro: switcher de workspace, `X-Workspace-Id`, usuarios filtrados y Vault por cartuchos activos.
2. Contrato config-only: validar configuración de cartuchos existentes sin apagar legacy.
3. Runtime genérico read-only: schema, semantic, watermarks, jobs, preview, catalog y lineage.
4. RLS operacional: cerrar tablas scoped y guard tests anti policies permisivas.
5. Extracción genérica: Vault scoped, DAG/job genérico y raw scoped.
6. MCP genérico: tools `cartridge_*` con contexto firmado, sin scope por args.
7. Deprecación legacy: apagar runtime por cartucho solo tras paridad y dos releases verdes.

## No Objetivos De Este Bloque

- No crear `/admin/tenants` ni `/admin/workspaces`.
- No duplicar onboarding de `/operations/companies`.
- No mover DAGs a `cartridges/platform/dags`.
- No eliminar MCP legacy por cartucho.
- No permitir contraseña manual para bootstrap de empresa.
