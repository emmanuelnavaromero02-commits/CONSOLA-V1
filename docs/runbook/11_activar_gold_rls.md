# 11 — Activar el backstop RLS de las tablas gold (P0-RLS-002)

La migración `infra/init_gold/40_gold_tenant_rls.sql` instala un backstop de
**Row-Level Security a nivel Postgres** sobre las tablas `gold_*` / `master_*`.
Se entrega **dormido** (permisivo por defecto): mientras `omega.rls_enforce`
no esté en `'on'`, no cambia ningún comportamiento. Esto añade una segunda capa
de aislamiento por debajo del filtro de aplicación (`_inject_rls_ast`) y de la
proyección forzada de scope (`_ensure_scope_columns`, P0-RLS-001).

## Qué hace la migración (ya verificado contra Postgres 16)
- `ENABLE` + `FORCE ROW LEVEL SECURITY` en cada `gold_*`/`master_*` con columnas
  `tenant_id` y `workspace_id`.
- Una policy `omega_tenant_isolation` con `USING` + `WITH CHECK`:
  - Permisiva si `omega.rls_enforce <> 'on'` (default).
  - Si está en `'on'`: solo filas donde `tenant_id = current_setting('omega.tenant_id')`
    **y** `workspace_id = current_setting('omega.workspace_id')`.
- Un **event trigger** que aplica la policy automáticamente a cada nueva tabla
  `gold_*`/`master_*` que cree Refinement.

Escenarios validados (psql, PostgreSQL 16): permisivo por defecto · aislamiento
al activar · **rechazo de INSERT con scope forjado** vía `WITH CHECK` · INSERT
legítimo permitido.

## Activación (NO hacer hasta validar en stack vivo)
La conexión DuckDB de Refinement es **larga y compartida**, y el `ATTACH ... (TYPE
postgres)` a `pggold` es por-conexión. Por eso los GUCs deben fijarse
**por request** (no por conexión) sobre la conexión Postgres correcta, lo cual
debe validarse contra el reúso de conexión de DuckDB antes de confiar en él.

Pasos:
1. **Wire Refinement** para que, en cada operación gold (lectura y materialize),
   ejecute sobre `pggold`:
   ```sql
   SET omega.tenant_id    = '<tenant del request>';
   SET omega.workspace_id = '<workspace del request>';
   ```
   y los limpie/re-fije por request. Verificar en el stack vivo que el scanner
   Postgres de DuckDB usa la misma sesión donde se fijó el GUC (probar con dos
   requests de tenants distintos sobre la misma conexión).
2. Solo cuando (1) esté validado, activar el enforcement:
   ```sql
   ALTER DATABASE modecissions_gold SET omega.rls_enforce = 'on';
   ```
   (o exportar el flag por sesión desde Refinement).
3. Smoke: un usuario del workspace A no debe ver ni escribir filas del workspace B
   en ninguna tabla `gold_*`, incluso forzando un dataset con `tenant_id`/`workspace_id`
   ajenos (debe ser rechazado por `WITH CHECK`).

## Rollback
La policy es permisiva por defecto; para desactivar el enforcement basta:
```sql
ALTER DATABASE modecissions_gold RESET omega.rls_enforce;
```
La infraestructura (policy + event trigger) puede quedar instalada sin efecto.
