# CSP `'unsafe-inline'` Migration Plan

**Status:** Proposed. Not started. Tracked as known debt in `README.md`.

## Goal

Remove `'unsafe-inline'` from both `script-src` and `style-src` in every
response served by `console/app/main.py` and `workspace/app/main.py`. The
final state should serve a CSP whose `script-src` and `style-src` use either
nonces (`'nonce-{random}'`) or hashes (`'sha256-...'`) — never `'unsafe-inline'`.

A passing CI check should fail any PR that re-introduces `'unsafe-inline'`
or any new inline `<script>`/`<style>` block in `*.html` under
`console/app/static/` or `workspace/app/static/`.

## Why this matters

The current XSS posture relies entirely on disciplined `esc()` / `escHtml()`
usage in every JS path that mutates `innerHTML`. Audits found and fixed
several real injection points already (`editUser` JSON.stringify, `u.role`,
`responsable` mixing). Each new feature is one missed escape away from
exploitable XSS, and `'unsafe-inline'` removes the CSP backstop that would
otherwise neutralise it. Browser DevTools also continue to warn that the
declared CSP is essentially advisory.

## Scope (inventory)

Static HTML files that contain inline `<script>` or `<style>` blocks:

```
console/app/static/index.html
console/app/static/login.html
console/app/static/me.html
console/app/static/admin_users.html
console/app/static/decisions.html
console/app/static/apps_gallery.html
console/app/static/rag.html
console/app/static/studio.html          (largest — ~6,000 lines of inline JS)
console/app/static/forgot_password.html
console/app/static/reset_password.html
console/app/static/viewers/dataset.html
console/app/static/viewers/datasets.html
console/app/static/viewers/job.html
console/app/static/viewers/jobs.html
console/app/static/viewers/pipeline.html
console/app/static/viewers/schema.html
console/app/static/viewers/semantic.html
console/app/static/viewers/vault.html
workspace/app/static/workspace.html
```

All `onclick="..."`, `onchange="..."`, and similar inline event handlers are
also blocked by strict CSP and need to migrate to `addEventListener` or
`data-*` + delegated handlers.

## Approach

**Recommended:** nonce-per-request via Jinja templates. Lower lift than
extracting every inline block to a `.js` file, keeps the existing edit-in-
HTML workflow alive, and lets us migrate page by page without a flag day.

### Phase 0 — Scaffolding (½ day)

1. Add `jinja2` to `console/requirements.txt` and `workspace/requirements.txt`.
2. Instantiate `Jinja2Templates(directory="app/static")` in each `main.py`.
3. Add a middleware that generates a 128-bit nonce per request, exposes it on
   `request.state.csp_nonce`, and injects it into the CSP `script-src` /
   `style-src` directives alongside `'unsafe-inline'` for the transition
   period (so unmigrated pages still work).
4. Add a context processor so templates can reference `{{ csp_nonce }}`
   without explicit plumbing in every route.

### Phase 1 — Per-page migration (1–2 days, page at a time)

For each HTML file:

1. Rename `.html` to a Jinja template; switch the route from `FileResponse`
   to `templates.TemplateResponse`.
2. Replace every `<script>` and `<style>` opening tag with
   `<script nonce="{{ csp_nonce }}">` / `<style nonce="{{ csp_nonce }}">`.
3. Replace inline event handlers (`onclick=`, `onchange=`, `oninput=`, etc.)
   with named handlers and one `<script nonce="...">` block at the bottom
   that wires them via `addEventListener`. Use `data-*` attributes to carry
   per-element state (`data-user-id`, `data-app-name`) rather than embedding
   JSON in `onclick`.
4. Add the page to a "migrated" list and run a Playwright smoke test that
   exercises every button and form on the page.

Migration order (smallest first, builds confidence):

1. `forgot_password.html`, `reset_password.html`, `login.html` (small, no auth)
2. `me.html`, `apps_gallery.html`, `rag.html` (medium)
3. `decisions.html`, `admin_users.html` (medium, lots of dynamic rows)
4. All `viewers/*.html` (read-only)
5. `index.html`, `workspace.html`
6. `studio.html` LAST — biggest, most fragile

### Phase 2 — Cutover (½ day)

1. Once every page renders with a nonce, drop `'unsafe-inline'` from both
   `SECURITY_HEADERS` and `VIEWER_SECURITY_HEADERS`.
2. Run the full Playwright suite against both services.
3. Spot-check in DevTools that no `Refused to execute inline script` warnings
   appear during normal use.

### Phase 3 — Lint guard (½ day)

1. Add a pre-commit/CI check (`scripts/check_no_inline_scripts.py`) that
   greps every `*.html` under `console/app/static/` and
   `workspace/app/static/` for:
   - `<script>` without `nonce="{{ csp_nonce }}"`
   - `<style>` without `nonce="{{ csp_nonce }}"`
   - `on[a-z]+="` attributes (inline handlers)
   And fails the build if any are present.
2. Add a test that fetches each route and asserts the response CSP header
   contains neither `'unsafe-inline'` in `script-src` nor in `style-src`.

## Backout

If a page breaks after migration and we need to ship a fix urgently, the
backout is to re-add `'unsafe-inline'` to `SECURITY_HEADERS` and revert the
specific template to its pre-migration `.html`. The middleware nonce is
harmless when no template consumes it.

## Out of scope

- Subresource Integrity (SRI) on third-party scripts (we don't ship any
  third-party scripts today, so no immediate value).
- Trusted Types policy. Worth considering after this migration but not part
  of this plan.
- Reporting endpoints (`report-uri` / `report-to`). Optional follow-up once
  the policy is strict enough that violations are signal, not noise.

## Estimate

- Phase 0: 0.5d
- Phase 1: 3–5d (studio.html alone is half of this)
- Phase 2: 0.5d
- Phase 3: 0.5d

Total: **5–7 engineering-days**, assuming a Playwright suite already exists
or one is acceptable as part of this work. Without a UI test harness the
risk of regressions makes this materially riskier.

## Decision needed before starting

- Do we already have Playwright or a similar end-to-end harness? If not, the
  first commit on this branch should be standing one up. Without it, every
  page migration is a manual click-test on staging.
- Do we want nonces or hashes? Nonces are simpler when content is templated;
  hashes are simpler when content is fully static. Recommend nonces.
- Should the same migration cover `workspace.html` in the same PR or split?
  Recommend split, because workspace and console deploy independently.
