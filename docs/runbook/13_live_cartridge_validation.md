# 13 - Live Cartridge Validation

P2-20 requires real sandbox credentials. Local fake upstreams, demo tokens, and
mocked unit tests do not count.

## Command

```bash
OMEGA_ENABLE_LIVE_CARTRIDGE_TESTS=1 \
OMEGA_LIVE_CARTRIDGE_CREDS_CONFIRMED=1 \
TEST_PASSWORD=<bootstrap-admin-password> \
make live-cartridge-tests
```

The script logs in to Console, then validates each priority cartridge with:

1. `POST /api/cartridges/{cartridge}/test_connection`
2. `POST /api/pipeline/{cartridge}/{entity}/extract`

Default minimal entities:

| Cartridge | Entity |
|---|---|
| `hubspot` | `deals` |
| `salesforce` | `Account` |
| `replicon` | `User` |
| `sap_hcm` | `EmployeeMaster` |
| `sap_s4hana` | `BusinessPartner` |
| `sap_successfactors` | `User` |

Override with `OMEGA_LIVE_ENTITY_<CARTRIDGE>` variables if a sandbox exposes a
different minimal entity.

## Credential Checklist

Before setting `OMEGA_LIVE_CARTRIDGE_CREDS_CONFIRMED=1`, verify that scoped
Vault connections or approved environment sources exist for:

- HubSpot private app token with CRM read scopes.
- Salesforce sandbox Connected App credentials and username/password token.
- Replicon tenant/company/user credentials.
- SAP HCM OData sandbox credentials.
- SAP S/4HANA OData sandbox credentials.
- SAP SuccessFactors OData sandbox credentials.

## Expected Evidence

A successful run must show `OK ... test_connection` and
`OK ... extraction triggered` for all six cartridges. Save the terminal output
under `docs/release-evidence/` for the release SHA.

If any credential is missing, keep P2-20 as BLOCKED and record the exact
missing cartridge and credential owner.
