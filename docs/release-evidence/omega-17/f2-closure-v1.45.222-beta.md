# F2 — CIERRE FORMAL (CI y cadena de artefactos)

**Estado: CLOSED** · Fecha de cierre: 2026-08-15 · Verificado por auditoría independiente (Claude) contra GitHub, no contra fixtures.

## Evidencia verificada directamente

| Criterio F2 | Estado | Evidencia |
|---|---|---|
| Versión publicable **completamente verde** | ✅ | Run de release `31889570253`, attempt 1, `status=completed / conclusion=success`, 2026-08-15T14:47:44Z |
| **Tag inmutable** | ✅ | Release `v1.45.222-beta` (id 371072769): `draft=false`, `prerelease=true`, **`immutable=true`** |
| Sobre el **commit canónico** | ✅ | `target_commitish = 34504a10f6fc7b944bb219ed4a8bb02b2dd907ab` (== `origin/main` HEAD) |
| **Manifiesto exacto + 2 assets** | ✅ | `omega-release-manifest-v1.45.222-beta.json` (sha256 `0151ece3…`) + su sidecar `.sha256` (sha256 `e25935e0…`), ambos `state=uploaded` |
| **15 imágenes ligadas al código** | ✅ (por pipeline) | Cuerpo del release: *"15 images bound to 34504a10… tested with exact source checkout bind mounts"*; el gate `digest-full-stack-gate` pasó dentro del run verde |
| **Digests verificables** | ✅ (por pipeline) | El contrato de publicación incluye "verificación remota de los 15 digests" y pasó como parte del run verde |
| **Imágenes privadas** | ✅ (por pipeline) | El paso "privacidad de paquetes" está en el contrato y pasó en el run verde |
| **Publicación sencilla, solo controles imprescindibles** | ✅ | PR #610 (squash) eliminó únicamente los `gh release verify*` que exigían attestations que el proyecto no genera; conservó comparación byte a byte, manifest/checksum, estado de release, privacidad y 15 digests |

## Contexto histórico (para el ledger)

- F2 estuvo **13 intentos en rojo** (`v1.45.210-beta` → `v1.45.221-beta`), todos por fallas del **entorno de publicación** (build hermético sin red, permisos de runtime, capacidad de disco del runner, timeouts de health, auto-candado de Docker, attestations inexistentes) — **nunca por el producto**: el `main` por-push se mantuvo verde todo el tiempo.
- La causa raíz fue **sobre-complejidad del pipeline de release**, no un bug del código. Se cerró **simplificando** el contrato (PR #606–#610), no agregando scripts.
- Última versión desplegable previa: `v1.45.209-beta` (13-ago). Ahora: **`v1.45.222-beta`**.

## Límites de esta verificación (honestidad)

- Verifiqué directamente desde la API de GitHub: estado del run, objeto release, inmutabilidad, los 2 assets y sus digests, y el commit objetivo.
- La **presencia y privacidad de los 15 digests en GHCR** y el **pull por digest del stack** los verificó el propio pipeline verde (evidencia in-pipeline), no un pull independiente desde este contenedor (paquetes privados, sin acceso directo).

## Veredicto

**F2 satisface sus criterios y queda CLOSED.** Se acabó el ciclo de arreglar-publicación-fallo-por-fallo. Siguiente: **F3 (P0/P1 y aislamiento multi-tenant)** en orden.
