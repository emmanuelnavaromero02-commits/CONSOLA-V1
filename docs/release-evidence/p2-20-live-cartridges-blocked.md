# P2-20 Live Cartridge Evidence

Status: BLOCKED

Branch: `codex/v1-hardening-gates`

## Implemented Gate

`make live-cartridge-tests` runs `scripts/run_live_cartridge_checks.sh`.
The script covers all priority cartridges:

- `hubspot`
- `salesforce`
- `replicon`
- `sap_hcm`
- `sap_s4hana`
- `sap_successfactors`

For each cartridge it validates:

1. `POST /api/cartridges/{cartridge}/test_connection`
2. `POST /api/pipeline/{cartridge}/{entity}/extract`

## Acceptance Command

```bash
OMEGA_ENABLE_LIVE_CARTRIDGE_TESTS=1 make live-cartridge-tests
```

## Output

```text
[live-cartridges] BLOCKED: OMEGA_LIVE_CARTRIDGE_CREDS_CONFIRMED=1 is required after loading real sandbox credentials
make: *** [live-cartridge-tests] Error 2
```

## Unblock

Load scoped sandbox credentials for HubSpot, Salesforce, Replicon, SAP HCM,
SAP S/4HANA, and SAP SuccessFactors, then run:

```bash
OMEGA_ENABLE_LIVE_CARTRIDGE_TESTS=1 \
OMEGA_LIVE_CARTRIDGE_CREDS_CONFIRMED=1 \
TEST_PASSWORD=<bootstrap-admin-password> \
make live-cartridge-tests
```

P2-20 remains BLOCKED until every cartridge reports `OK ... test_connection`
and `OK ... extraction triggered`.
