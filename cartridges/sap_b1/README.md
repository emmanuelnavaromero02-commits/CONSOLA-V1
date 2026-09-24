# SAP Business One Cartridge

FastAPI cartridge that reads SAP Business One 10 company databases over SQL
and lands them in the MODecissions Bronze layer as parquet. Business One
keeps one database (one HANA schema) per company; the cartridge reads every
configured company in one run and stamps each row with the company alias.

| Property | Value |
| --- | --- |
| **Cartridge ID** | `sap_b1` |
| **Source** | SAP HANA (`hdbcli`) in production; a Business One-shaped Postgres test bed in CI |
| **Protocol** | SQL, read-only session, bound parameters only |
| **Port** | `8206` (matches `Dockerfile` CMD) |
| **Internal auth** | `X-Internal-Api-Key` header |

Nothing customer-specific lives in this repository: hosts, schema names,
users and passwords are runtime configuration (environment or Console
Vault, connection `sap_b1/default`).

## What it reads

43 tables, named as in Business One, in [`app/config/entities.yaml`](app/config/entities.yaml):

| Group | Tables | Reading rule |
| --- | --- | --- |
| Company & finance masters | `CINF`, `OADM`, `OCRN`, `ORTT`, `OACT`, `OFPR`, `OPRC` | snapshot (`mode: full`) |
| Partners, items, warehouses | `OCRD`, `OITM`, `OITT`/`ITT1` (stamped); `OCRG`, `OSLP`, `OWHS`, `OITB` (snapshot) | `UpdateDate` + `UpdateTS` |
| Sales documents | `OINV`/`INV1`, `ORIN`/`RIN1`, `ODLN`/`DLN1`, `ORDN`/`RDN1`, `ORDR`/`RDR1` | header stamp; lines through the header |
| Purchase documents | `OPCH`/`PCH1`, `ORPC`/`RPC1`, `OPDN`/`PDN1`, `OPOR`/`POR1` | header stamp; lines through the header |
| Journal | `OJDT`/`JDT1` | header stamp; lines through the header |
| Inventory & batches | `OINM` (`TransNum`, paged by `TransNum, TransSeq`), `IBT1` (`LogEntry`); `OITW`, `OBTN`, `OBTQ`, `OIBT` | integer watermark / snapshot |
| Production | `OWOR`/`WOR1` | header stamp; lines through the header |

### Incremental reads

* **Header tables** carry `UpdateDate` (a date) and `UpdateTS` (an integer
  `HHMMSS`). The watermark is the pair, persisted as `YYYY-MM-DDTHH:MM:SS`
  in the source clock, and read back as
  `UpdateDate > d OR (UpdateDate = d AND UpdateTS >= t)` after a 5 minute
  back-off. Duplicates this creates are resolved downstream by
  `_source_updated_at`.
* **Line tables** (`INV1`, `JDT1`, `WOR1`, `ITT1`, ...) have no stamp of
  their own. They are read through a join to their header and inherit its
  stamp, so an edited document brings back all of its lines.
* **`OINM`** never changes once written; `TransNum` is the watermark. One
  document is one transaction with one row per line, so pages are keyed on
  `(TransNum, TransSeq)`; a page keyed on `TransNum` alone would drop lines
  at every page cut. **`IBT1`** likewise never changes and is read by its
  `LogEntry` identity.
* **Snapshot tables** (`OITW`, `OBTQ`, `OIBT`, `ORTT`, ...) are read whole
  every run; `OIBT` duplicates `OBTQ` and can be disabled in `entity_config`
  once the customer confirms which one Business One maintains.
* Watermarks are tracked per entity **and** company (`OINV@mx_mfg`) in
  `entity_watermarks`. A full load also records the watermark so the next
  incremental cycle continues from it. The recorded watermark never passes
  the source clock read when the run started, so a document edited behind
  the cursor during a long run is still reached by the next cycle.
* Each company is flushed to Bronze and its watermark committed before the
  next company starts. A company whose schema fails leaves the run
  `failed` without making the others re-read what they already delivered.
* Pages are keyset pages on the primary key, never `OFFSET`. Only `CINF`
  (one row) is read without a key.

Every row carries two extra columns: `_company` (the alias from
`SAP_B1_COMPANIES`) and `_source_updated_at` (the header stamp, or null on
snapshot tables). Every file of an entity is written with the schema
declared in `column_types` (`decimal(19,6)` amounts, `timestamp` dates,
`int64`, `string`), so an all-null column or an empty batch never changes
the type a reader sees across files.

### Deletions

Business One does not stamp deletions. Lines removed from an open order,
planned components of a production order, whole bills of materials and
unused partners or items simply stop appearing. Bronze keeps the old rows
with their old stamp. The rule for silver: per `(_company, DocEntry)` keep
only the lines whose `_source_updated_at` equals the latest header stamp
(lines inherit it, so a re-read document replaces all of its lines); for
masters and bills of materials, a periodic full load and a
"not seen in the latest snapshot" reconciliation.

### Initial load

Run the initial load entity by entity (the MCP `extract` job or the
`sap_b1_extract` DAG), not with `extract-all`: with 43 tables across every
company a synchronous `extract-all` outlives any HTTP client timeout, and a
retried DAG task would start a second run of the same entities while the
first is still reading.

An incremental cycle that finds no changes is a success with zero rows and
writes no file. A connection or query failure marks the run `failed` and
raises; it is never reported as zero rows.

## Configuration

| Variable | Meaning |
| --- | --- |
| `SAP_B1_DIALECT` | `hana` (default) or `postgres` (the test bed) |
| `SAP_B1_HOST` | database host reachable from the container (VPN / tunnel) |
| `SAP_B1_PORT` | tenant SQL port; empty means `30015` (hana) or `5432` (postgres) |
| `SAP_B1_USER`, `SAP_B1_PASSWORD` | read-only database user |
| `SAP_B1_DATABASE` | HANA tenant database when connecting through SYSTEMDB; Postgres database name for the test bed |
| `SAP_B1_COMPANIES` | `alias=SCHEMA,alias=SCHEMA`; aliases are lowercase identifiers and are what the lakehouse sees |
| `SAP_B1_ENCRYPT`, `SAP_B1_SSL_VALIDATE_CERTIFICATE` | HANA TLS settings (default on) |

The same values can be stored in the Console Vault as connection
`sap_b1/default` with the field names `dialect`, `host`, `port`, `user`,
`password`, `database`, `companies`. Until the console allow-lists
`cartridge-sap_b1` (deploy step 2 below) the Vault path is not reachable
and only the environment variables are used.

The database host is normally a private address behind the customer's VPN,
so the HTTP egress guard used by the OData cartridges does not apply: this
is a database session to a destination fixed by configuration.

## Connection kit

[`connect/`](connect/) holds what the customer's IT and the platform team run
to bring a Business One instance online. Every file is publishable
(placeholders only; `tests/test_connect_kit.py` checks for addresses, hosts,
schema names and secrets):

* `hana/`: HANA SQL to find the tenant SQL port, create / verify / revoke the
  read-only user, plus connectivity checks for the customer's Windows server
  (`test_connection.ps1`) and a Linux host (`test_connection.sh`).
* `config/`: the `SAP_B1_*` environment template, the Console Vault
  connection template, the `entity_scheduler` script that puts every entity
  on a two-hour cadence (psql `:tenant_id` / `:workspace_id`), and the
  24-month initial-load runbook (one entity-month per run, watermark seeding
  afterwards).
* `vpn/`: the recommended WireGuard tunnel from the customer's server to the
  VPN bastion: runbook (Spanish), server install script, security-group
  script, client config template.
* `windows/`: the alternative Windows connector, described only; it is built
  under `connect/windows-agent/` separately.

Two facts the runbooks call out: a run reaches the cartridge unscoped unless
its `security_context` is signed (the thin `sap_b1_extract` DAG forwards
`tenant_id` / `workspace_id` but does not sign them yet, unlike the
SuccessFactors DAG), and `historical` runs record no watermark.

## Running against the test bed

The Business One-shaped Postgres fake in `tests/fixtures/sap_b1` (schema,
deterministic generator, loader) is the reference source. The cartridge
tests start `postgres:15` in Docker, load the fake and read it through
`extraction_service.run_entity`; they skip without Docker.

```bash
python -m pytest cartridges/sap_b1 -q
```

## Endpoints

* `GET /health`, `GET /healthz`, `GET /health/sap_b1` (per-company reachability and Business One version)
* `GET /skills/list`, `POST /skills/test_connection`, `/skills/run_*`
* Console aliases: `/entities`, `/entities/{entity}/schema|preview|extract`, `/extract-all`, `/runs`, `/watermarks`
* MCP: `/mcp/rpc` (Streamable HTTP), `/mcp/tools`, `/mcp/invoke`

## To validate on the customer's HANA before the first load

* `UpdateDate` / `DocDate` column types against the `datetime` parameters
  the cartridge binds (Business One keeps them as `TIMESTAMP` at midnight).
* `OINM.TransSeq` (the row number within a stock transaction) and
  `IBT1.LogEntry` (the batch-transaction identity) exist under those names.
* `SELECT CURRENT_TIMESTAMP FROM DUMMY` runs on the same clock Business One
  uses for `UpdateDate`/`UpdateTS`; if the server clock is UTC and the
  application stamps local time, the cap on the watermark must be shifted.
* `OITT.ToWH`, the `OINM` view columns, `OFPR.Indicator`; that `hdbcli`
  installs in the image.

## Deploying (checklist, not done by this cartridge alone)

1. Database role `omega_cartridge_sap_b1` (migration
   `99zzzzl_sap_b1_cartridge_role.sql`): needs `OMEGA_CARTRIDGE_SAP_B1_PASSWORD`
   in the host `.env`; the migration skips role creation with a warning when the
   password is absent and can be re-run once it is set.
2. `INTERNAL_API_KEY_SAP_B1_TO_CONSOLE` in the host `.env` and in the
   console's environment, plus the console allow-list entry for
   `cartridge-sap_b1` (console `auth.py` and the Vault reveal map). Not part
   of this cartridge: the console refuses to start in production when a
   listed key is missing. Refinement already accepts `cartridge-sap_b1` with
   the shared cartridge key.
3. Console registries that name the cartridge for the copilot and the
   operations page (MCP builtin list, microservice probes), and the
   console-next `KNOWN_CARTRIDGES` list (needs the committed export rebuilt).
4. The `sap-b1` service in the AWS cartridges compose, the Airflow DAG mount
   there and the release image matrix; network path from the host to the
   HANA tenant port.
