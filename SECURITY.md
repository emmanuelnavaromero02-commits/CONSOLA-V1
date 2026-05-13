# OMEGA — Security policy and operations

## Secrets management

OMEGA never stores secrets in the git repository. All secrets live in
`infra/.env`, which is gitignored and generated locally by
`bash infra/bootstrap.sh`.

### Initial setup

```
bash infra/bootstrap.sh                    # generates infra/.env with random secrets
bash infra/bootstrap-keys.sh infra/.env    # adds 11 per-pair internal API keys (Sprint v1.12)
make up
```

The generated `infra/.env` is created with `umask 077` so only the
invoking user can read it. Do not change those permissions.

### Rotating secrets

If a secret leaks, or as part of routine rotation:

```
make rotate-keys
make down && make up
```

`make rotate-keys` does the following, in order:

1. Backs up the current `infra/.env` to `infra/.env.save` (gitignored).
2. Removes `infra/.env`.
3. Re-runs `infra/bootstrap.sh` to generate fresh top-level secrets
   (`INTERNAL_API_KEY`, `JWT_SECRET_KEY`, `POSTGRES_PASSWORD`, etc.).
4. Re-runs `infra/bootstrap-keys.sh` to generate fresh per-pair
   internal API keys (the 11 `INTERNAL_API_KEY_*_TO_*` secrets
   introduced in Sprint v1.12).

After `make down && make up`:

- All active user sessions are invalidated (JWT signing key rotated).
- All internal service-to-service calls are re-authenticated with new keys.
- Once you've verified the stack is healthy, delete `infra/.env.save`.

### Audit verification

The test suite enforces the following contract:

- `.env` files are never tracked by git → `test_no_env_file_is_currently_tracked`
- `.env` files have never been added in git history → `test_no_env_file_in_git_history`
- `.gitignore` blocks `.env` and `.env.local` patterns → `test_gitignore_blocks_env_files`

Run:

```
pytest tests/test_env_never_in_git.py -v
```

These tests must remain green in CI. If any of them fail, treat it as a
secrets-exposure incident: rotate every secret the offending file
contained, then scrub the file from git history with BFG or
`git filter-repo`, and only then re-run the tests.

### Where credentials really live

| Credential type | Storage | How it gets there |
|---|---|---|
| Internal API keys (12: legacy + 11 per-pair) | `infra/.env` | `infra/bootstrap.sh` + `infra/bootstrap-keys.sh` (random hex) |
| JWT signing key, Postgres / MinIO / Superset / Airflow passwords | `infra/.env` | `infra/bootstrap.sh` (random hex) |
| LLM API keys (Gemini / Anthropic / OpenAI / Google) | DB via Vault service | Settings UI; never hardcoded in `.env` |
| Cartridge integration creds (Replicon / SAP / SuccessFactors / Outlook) | DB via Vault service | Settings UI; `.env` keys are kept only for legacy local-dev fallback |

The Vault service (`vault/app/main.py`) is the only system component
that holds long-lived integration secrets at rest; the rest of the
stack pulls them through it on demand.

## Reporting a vulnerability

Email `security@<your-domain>` with details. Include reproduction steps
when possible. Do NOT file public GitHub issues for security concerns —
use email so we can coordinate a fix before disclosure.
