# 04 — Configurar los cartuchos SAP (HCM / S4 / SuccessFactors / Business One)

> Audiencia: admin que va a conectar OMEGA a un sistema SAP existente.
> Aplica a los 3 cartuchos SAP OData (`sap_hcm`, `sap_s4hana`,
> `sap_successfactors`); el flujo es idéntico — sólo cambian los
> nombres de variables y endpoints. SAP Business One (`sap_b1`) lee la
> base de datos por SQL, no OData: ver la sección al final.

## Pre-requisitos

- Tenant creado ([02](02_primer_tenant.md)).
- Credenciales reales del sistema SAP correspondiente.
- En v1.0 `make up` levanta el perfil SAP por defecto. Si usaste
  `make up-core` o un compose manual y los servicios `sap-*` no aparecen en
  `docker compose ps`, relanza:
  ```bash
  docker compose -f infra/docker-compose.yml --profile sap up -d
  ```

## Tabla de variables por cartucho

| Cartucho            | Setting key (en /settings)        | Origen      |
|---------------------|-----------------------------------|-------------|
| **sap_hcm**         | `sap_hcm_base_url`                | SAP basis   |
|                     | `sap_hcm_user`                    | RFC user    |
|                     | `sap_hcm_pass`                    | RFC pass    |
|                     | `sap_hcm_client_mandant` (default `100`) | – |
| **sap_s4hana**      | `sap_s4_base_url`                 | OData v2/v4 |
|                     | `sap_s4_user` + `sap_s4_pass`     | técnico     |
|                     | `sap_s4_api_key`                  | si aplica   |
| **sap_successfactors** | `sf_base_url` + `sf_company_id`   | tenant      |
|                     | `sf_client_id` + `sf_client_secret` + `sf_token_url` | OAuth client credentials |
|                     | `sf_auth_method=saml_bearer_assertion` + `sf_client_id` + `sf_admin_user` + `sf_private_key_path` + `sf_token_url` | OAuth SAML Bearer Assertion vía `/oauth/idp` |

## Pasos (idéntico para los 3)

### 1. Cargar secretos en /settings

http://localhost:8000/settings → categoría `integrations` → set cada
key de la tabla anterior. Marcar `is_secret=true` para passwords /
tokens / client_secret.

Cifrado Fernet en reposo. Cada set queda en `audit_events` con `ip`,
`user_agent` y `action=settings.update`.

### 2. Test connection

`/cartridges` → tarjeta `sap_*` → `Test connection`.

- ✅ `ok` — handshake con OData metadata.
- ⚠️ `degraded` — endpoint responde pero metadata vacía. URL apunta
  al server correcto pero a un service path inválido.
- ❌ `error` — leer el `message`:
  - `401` → user/pass incorrectos
  - `404` → base_url o service path mal puesto
  - `502 Bad Gateway` → el SAP backend está caído (no es OMEGA)

### 3. Listar entidades disponibles

`/cartridges/{c}/entities` (la tabla del wizard). El cartucho consulta
`entity_config` en Postgres + `get_watermarks` del propio cartucho:

```bash
curl -fsS -H "Cookie: session=$SESSION" \
  http://localhost:8000/api/cartridges/sap_hcm/entities
```

### 4. Ejecutar carga

Para HCM: elegir `pa0001` (datos maestros empleado) → `Full`. Confirm.

Para S4: `BusinessPartner` o `MaterialMaster` → `Full`.

Para SF: `PerPerson` → `Full`.

### 5. Verificar

```sql
SELECT cartridge_id, entity_name, status, records_extracted
FROM extraction_runs
WHERE cartridge_id LIKE 'sap_%'
ORDER BY started_at DESC LIMIT 10;
```

## Si algo falla

- **`test_connection` devuelve `degraded` pero credenciales son
  correctas**: el `base_url` apunta a la página HTML del Fiori Launchpad
  en vez de a `/sap/opu/odata`. Corregir.
- **Run de pa0001 con 0 records**: filtro de `mandant` mal puesto.
  Revisa `sap_hcm_client_mandant`.
- **Cargas lentas (>30 min)**: SAP corp tiene rate limits agresivos.
  Bajar `page_size` en `entity_config` y/o re-correr en horario
  off-peak.

## SAP Business One (`sap_b1`)

Business One no expone OData: el cartucho lee los esquemas de cada compañía
por SQL (HANA en producción, `hdbcli`) y sube Bronze por entidad y compañía.
El flujo de arriba (settings → test connection → entidades → carga) es el
mismo, pero las variables y el modelo de compañías son distintos.

- Referencia completa del cartucho, variables `SAP_B1_*` y lista de
  despliegue: [`cartridges/sap_b1/README.md`](../../cartridges/sap_b1/README.md).
- Guía de conexión al tenant HANA (puerto, usuario de sólo lectura,
  mapa `alias=SCHEMA` de compañías): [`cartridges/sap_b1/connect/`](../../cartridges/sap_b1/connect/).
- En AWS el servicio `sap-b1` vive en `docker-compose.cartridges.yml`; exige
  `INTERNAL_API_KEY_SAP_B1_TO_CONSOLE` y `OMEGA_CARTRIDGE_SAP_B1_PASSWORD`
  en el `.env` del host (y en Secrets Manager), y el rol
  `omega_cartridge_sap_b1` lo crea `infra/init/99zzzzl_sap_b1_cartridge_role.sql`
  sólo cuando esa contraseña ya existe; la migración se puede re-ejecutar.
- Los valores del cliente (host, esquemas, credenciales) nunca van al repo.

## SAP SuccessFactors: variantes de OAuth

SuccessFactors soporta dos modos en OMEGA. El modo por defecto sigue
siendo `oauth2_client_credentials`; no cambia el flujo existente.

### Variante A: OAuth2 client credentials

Usa esta variante cuando SAP te entrega `client_id` + `client_secret`.

Configura en Vault/Settings o variables de entorno:

| Campo Vault / env | Descripción |
|---|---|
| `auth_method` / `SF_AUTH_METHOD=oauth2_client_credentials` | Opcional; es el default |
| `sf_base_url` / `SF_BASE_URL` | URL OData v2 de SuccessFactors |
| `sf_company_id` / `SF_COMPANY_ID` | Company ID del tenant |
| `sf_client_id` / `SF_CLIENT_ID` | OAuth client ID |
| `sf_client_secret` / `SF_CLIENT_SECRET` | OAuth client secret |
| `sf_token_url` / `SF_TOKEN_URL` | Token endpoint |

### Variante B: OAuth2 SAML Bearer Assertion (X.509)

Usa esta variante cuando SAP entrega un certificado X.509 para el flujo
SAML Bearer. OMEGA no construye ni firma XML-DSig localmente: llama al
endpoint oficial legacy de SuccessFactors `/oauth/idp` para generar el
SAML assertion server-side y luego lo intercambia en `/oauth/token`.
El token se cachea hasta `expires_in`.

Configura en Vault/Settings o variables de entorno:

| Campo Vault / env | Descripción |
|---|---|
| `auth_method` / `SF_AUTH_METHOD=saml_bearer_assertion` | Activa SAML bearer |
| `sf_base_url` / `SF_BASE_URL` | URL OData v2 de SuccessFactors |
| `sf_company_id` / `SF_COMPANY_ID` | Company ID del tenant |
| `sf_client_id` / `SF_CLIENT_ID` | OAuth client ID, usado como issuer |
| `sf_admin_user` / `SF_ADMIN_USER` | `user_id` enviado a `/oauth/idp` |
| `sf_token_url` / `SF_TOKEN_URL` | Token endpoint, normalmente `https://<api-server>/oauth/token` |
| `sf_idp_url` / `SF_IDP_URL` | Opcional; default derivado de `SF_TOKEN_URL` como `https://<api-server>/oauth/idp` |
| `sf_private_key_path` / `SF_PRIVATE_KEY_PATH` | Ruta del PEM dentro del contenedor; default `/run/secrets/sf_epiuse_iaappliance_connector.pem` |

Para despliegues con Vault, guarda `auth_method`, `admin_user` y
`private_key_path` en la conexión scoped del cartucho. Si necesitas un
endpoint IDP no estándar, guarda también `idp_url`. Si el operador
necesita inyectar el contenido PEM desde un secreto gestionado, puede
usar la clave `SF_PRIVATE_KEY_PEM` en Vault/worker secret; no debe
aparecer en logs ni en git.

El POST a `/oauth/idp` usa `application/x-www-form-urlencoded` con:

```text
client_id=<SF_CLIENT_ID>
user_id=<SF_ADMIN_USER>
token_url=<SF_TOKEN_URL>
private_key=<contenido PEM>
```

El POST a `/oauth/token` usa:

```text
company_id=<SF_COMPANY_ID>
client_id=<SF_CLIENT_ID>
grant_type=urn:ietf:params:oauth:grant-type:saml2-bearer
assertion=<respuesta de /oauth/idp>
```

SAP ha señalado que `/oauth/idp` expone la private key al endpoint y lo
considera legacy/deprecado para algunos escenarios. Si el cliente tiene
un IdP corporativo confiable, úsalo para generar el assertion fuera de
OMEGA; este modo existe para compatibilidad con tenants que todavía
dependen del endpoint oficial de SuccessFactors.

### Subir el PEM a AWS sin commitearlo

Nunca commitees `.pem` ni `.key`. El repo bloquea `*.pem`, `*.key` y
`secrets/*.pem` en `.gitignore`.

1. Guarda el PEM localmente fuera del repo o bajo `secrets/`:

   ```bash
   mkdir -p secrets
   chmod 700 secrets
   cp /ruta/segura/sf_epiuse_iaappliance_connector.pem secrets/
   chmod 400 secrets/sf_epiuse_iaappliance_connector.pem
   ```

2. Sube el contenido a SSM Parameter Store como SecureString:

   ```bash
   aws ssm put-parameter \
     --name /modecissions/prod/sap_successfactors/private_key_pem \
     --type SecureString \
     --value file://secrets/sf_epiuse_iaappliance_connector.pem \
     --overwrite
   ```

3. En el host AWS, materializa el PEM durante el bootstrap/deploy y
   móntalo en el cartucho:

   ```bash
   sudo install -d -m 0700 /opt/modecissions/secrets
   aws ssm get-parameter \
     --name /modecissions/prod/sap_successfactors/private_key_pem \
     --with-decryption \
     --query Parameter.Value \
     --output text \
     | sudo tee /opt/modecissions/secrets/sf_epiuse_iaappliance_connector.pem >/dev/null
   sudo chmod 0400 /opt/modecissions/secrets/sf_epiuse_iaappliance_connector.pem
   ```

4. Exporta sólo la ruta del host y la ruta interna antes de levantar el
   compose de cartuchos:

   ```bash
   export SF_AUTH_METHOD=saml_bearer_assertion
   export SF_PRIVATE_KEY_HOST_PATH=/opt/modecissions/secrets/sf_epiuse_iaappliance_connector.pem
   export SF_PRIVATE_KEY_PATH=/run/secrets/sf_epiuse_iaappliance_connector.pem
   docker compose \
     -f infra/terraform/deploy/docker-compose.aws.yml \
     -f infra/terraform/deploy/docker-compose.cartridges.yml \
     --profile sap up -d sap-successfactors
   ```

Si `SF_PRIVATE_KEY_HOST_PATH` no está presente, el mount queda apuntando
a `/dev/null` y el flujo `client_credentials` sigue funcionando. El modo
`saml_bearer_assertion` sí exige una clave PEM válida y falla cerrado si
no puede leerla.
