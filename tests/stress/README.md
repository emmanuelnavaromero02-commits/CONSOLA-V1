# OMEGA Stress Profile

This profile uses Locust to stress real authenticated console flows, not raw
`while true` traffic. It is intentionally guarded so local runs do not trigger
destructive jobs unless explicitly enabled.

## Install

```bash
.venv/bin/python -m pip install -r tests/stress/requirements.txt
```

## Safe Local Run

Start the stack first:

```bash
make up
make acceptance
make stress
```

Defaults:

- `OMEGA_STRESS_USERS=10`
- `OMEGA_STRESS_SPAWN_RATE=2`
- `OMEGA_STRESS_RUN_TIME=2m`
- `OMEGA_STRESS_ENABLE_WRITES=0`
- `OMEGA_STRESS_FAKE_HUBSPOT=1`

Reports are written to `artifacts/stress/<timestamp>/`.

## More Pressure

```bash
OMEGA_STRESS_USERS=50 \
OMEGA_STRESS_SPAWN_RATE=5 \
OMEGA_STRESS_RUN_TIME=10m \
make stress
```

## Include Extract Writes

This creates HubSpot extraction jobs. The default write profile serializes
triggers so the test measures end-to-end behavior without every user
overwriting the same parquet object at once.

```bash
OMEGA_STRESS_ENABLE_WRITES=1 \
OMEGA_STRESS_USERS=20 \
OMEGA_STRESS_RUN_TIME=5m \
make stress
```

The runner points HubSpot at `tests/fixtures/fake_hubspot_api.py` by default,
so local stress does not hit the real HubSpot API.

Gold refresh writes are separate because they rewrite derived datasets:

```bash
OMEGA_STRESS_ENABLE_WRITES=1 \
OMEGA_STRESS_ENABLE_GOLD_REFRESH=1 \
make stress
```

To deliberately attack concurrent writes, opt in explicitly:

```bash
OMEGA_STRESS_ENABLE_WRITES=1 \
OMEGA_STRESS_CONCURRENT_WRITES=1 \
make stress
```

## Live/Staging

Only use this when staging credentials and monitoring are ready:

```bash
OMEGA_STRESS_LIVE=1 \
OMEGA_STRESS_FAKE_HUBSPOT=0 \
OMEGA_STRESS_HOST=https://console.example.com \
OMEGA_STRESS_USERS=50 \
OMEGA_STRESS_RUN_TIME=15m \
make stress
```

## What It Exercises

- Login with real CSRF/session cookies.
- Console surfaces: `/api/me`, settings, cartridges.
- Control Room and KPIs.
- Gold dataset query: `/api/data/pipeline_salud`.
- Semantic/catalog/lineage for HubSpot.
- Studio live OpenAPI introspection with field types.
- HubSpot `test_connection` against fake/live upstream.
- Forged workspace isolation probe: must not return rows.
- Optional HubSpot extract load.
- Optional Gold refresh load.
- Optional Copilot write load.

## Failure Policy

The run fails on:

- unexpected 5xx/4xx for required endpoints,
- HubSpot `test_connection` not OK when fake upstream is enabled,
- loss of Studio field typing,
- forged workspace returning rows,
- failed HubSpot extraction jobs,
- Locust request failures.
