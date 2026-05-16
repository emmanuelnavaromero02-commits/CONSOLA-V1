# 07 — Debug de fallos (correlación con X-Request-ID, audit forense)

> Audiencia: operador que recibe "esto no funciona" de un usuario y
> necesita reconstruir qué pasó en los últimos minutos.

## El kit de debugging v1.41.1

Tres fuentes correladas:

| Fuente | Qué te dice | Cómo se identifica |
|---|---|---|
| Logs estructurados | qué ocurrió internamente, errores, latencia | `request_id` JSON field |
| `audit_events`     | qué acción admin sensible se ejecutó       | `ip`, `user_agent`, `action` |
| `extraction_runs`  | qué corrida de cartucho falló              | `run_id`, `cartridge_id`, `entity_name` |

Todas pueden cruzarse por **timestamp** + **request_id** (si la acción
nace de la consola web).

## 7.1 Caso A — "Me apareció un error rojo en la consola"

### Paso 1. Captura el X-Request-ID

Pídele al usuario que reproduzca y abra DevTools → Network → la
request fallida → Response Headers → copiar `X-Request-ID`.

Cada respuesta de OMEGA, incluida la del rechazo de auth, lleva esa
cabecera (Sprint v1.41.1 — `RequestIDMiddleware` en los 5 servicios).

### Paso 2. Busca en los logs

```bash
docker compose logs console 2>&1 | grep '"request_id":"<RID>"'
```

Como los logs son JSON estructurado, esto te da TODAS las líneas que
nacieron de esa request — desde el middleware hasta los handlers que
emitió. Filtra por `level: ERROR` para acortar:

```bash
docker compose logs console 2>&1 \
  | grep '"request_id":"<RID>"' \
  | grep '"level":"ERROR"'
```

### Paso 3. Cruza con audit si la acción es admin

```sql
SELECT action, status, ip, user_agent, metadata
FROM audit_events
WHERE created_at >= now() - INTERVAL '15 minutes'
  AND user_id = <id_del_usuario>
ORDER BY created_at DESC;
```

Esto te da qué intentó hacer + desde qué IP / browser.

### Paso 4. Si la falla es de cartucho

```sql
SELECT run_id, cartridge_id, entity_name, status, error_message,
       started_at, finished_at
FROM extraction_runs
WHERE started_at >= now() - INTERVAL '1 hour'
  AND status = 'failed'
ORDER BY started_at DESC;
```

El `error_message` suele tener el HTTP status o la causa raíz.

## 7.2 Caso B — "La consola está lenta"

```bash
# 1. ¿qué servicio está cargado?
docker stats --no-stream

# 2. ¿hay request_id repitiéndose miles de veces?
docker compose logs console 2>&1 | jq -r '.request_id' | sort | uniq -c | sort -rn | head
# si UN request_id tiene >100 líneas, es un loop. Ese log line le dice qué función.

# 3. ¿hay DB connection starvation?
docker exec mode_postgres psql -U postgres -c "SELECT pid, query, state, wait_event FROM pg_stat_activity ORDER BY query_start;"
```

Sospechosos típicos:

- DAG de Airflow corriendo cada minuto y saturando el pool de DB.
- `extraction_runs` con un `running` zombie nunca cerrado bloqueando
  watermarks.
- Refinement con una query DuckDB pegada.

## 7.3 Caso C — "El run de Replicon nunca termina"

```sql
SELECT run_id, status, started_at, finished_at, error_message
FROM extraction_runs
WHERE cartridge_id='replicon'
ORDER BY started_at DESC LIMIT 5;
```

Si `status='running'` y `started_at` es de hace >30 min, el run
quedó huérfano. Lo más probable: el scheduler de Airflow murió.

```bash
docker compose ps airflow-scheduler   # ¿(healthy)?
docker compose logs airflow-scheduler --tail 200
```

Restart:

```bash
docker compose restart airflow-scheduler
```

Forzar cierre manual del run zombie (no recupera datos, sólo libera el
estado para reintento):

```sql
UPDATE extraction_runs
SET status='failed', finished_at=now(),
    error_message='killed by operator — scheduler dead'
WHERE run_id='<RID>';
```

## 7.4 Caso D — "Un admin hizo algo que no debió"

Audit forense de ese usuario (v1.41.0 — `ip` + `user_agent` siempre
poblados):

```sql
SELECT created_at, action, resource_type, resource_id,
       ip, user_agent, status, metadata
FROM audit_events
WHERE email = 'sospechoso@acme.com'
  AND created_at >= '2026-05-10'
ORDER BY created_at DESC;
```

Combinado con los logs de la consola filtrados por `user_id` de
estructurado JSON, reconstruyes la timeline completa.

## Atajos curl

```bash
# manda un X-Request-ID conocido y rastréalo en logs
curl -fsS -H "X-Request-ID: forense-2026-05-16-001" \
  http://localhost:8000/healthz

docker compose logs console | grep "forense-2026-05-16-001"
```

## Si nada cuadra

1. `docker compose ps` — algún unhealthy te dice por dónde empezar.
2. Si todos healthy pero la queja persiste, revisa la
   [02_primer_tenant](02_primer_tenant.md) verification — quizá el
   usuario no tiene workspace membership.
3. Reproduce con tu sesión admin y captura el X-Request-ID — pasa a
   7.1.
