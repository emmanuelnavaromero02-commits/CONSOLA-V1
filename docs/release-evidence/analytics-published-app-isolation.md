# Published app isolation — offensive regression evidence

Evidence for the P0 C1 reported on `1a8ef430`: the Analytics viewer framed
`/apps/{name}` — externally-authored HTML — as a same-origin document with
`sandbox="allow-scripts allow-same-origin allow-downloads"`, so a malicious
published app ran with the console's own origin.

Method: a hostile published app in an isolated harness, driven by a real
Chromium (Playwright 1.62.1, Chrome Headless Shell 151). The wrapper HTML, the
content HTML and both CSP headers are produced by the real
`console/app/domains/apps/embed.py` — nothing about the boundary is
re-implemented for the test. Every backend request is logged server-side, so
"did it reach the backend" is answered by the server, not by the page.

## A/B: the same harness against both configurations

| | pre-fix `1a8ef430` | candidate |
|---|---|---|
| attempts blocked | 0 of 20 | **20 of 20** |
| shell DOM intact | **no** — mutated | yes |
| report reached the shell | yes (same-origin postMessage) | no |
| self-reload loop | **yes** — reloaded itself indefinitely | no |
| authenticated backend hits | **11,761**, all with session cookies | 1 (the declared dataset) |
| `/api/data/undeclared_secret` | **3,595 hits** | never reached |
| `/api/data/undeclared_secret/query` | **2,336 hits** | never reached |
| `/api/me` | **5,830 hits** | never reached |

The pre-fix run reproduces C1's finding and then some: attempt 8 (reload
without sandbox) succeeded, and each reload re-ran the whole exfiltration.

## The 20 attempts against the candidate

| # | Attempt | Result | Reached backend | Public message |
|---|---|---|---|---|
| 1 | `parent.document` | BLOCKED | no | `Failed to read a named property 'document' from 'Window'` |
| 2 | parent DOM mutation | BLOCKED | no | same — shell marker unchanged |
| 3 | shell `localStorage` | BLOCKED | no | frame-local shim returns `null` for the shell's key |
| 4 | shell `sessionStorage` | BLOCKED | no | as above |
| 5 | `document.cookie` | BLOCKED | no | `Failed to read the 'cookie' property from 'Document'` |
| 6 | CSRF token via parent | BLOCKED | no | `Failed to read a named property 'document'` |
| 7 | `removeAttribute("sandbox")` | BLOCKED | no | `frameElement is null (cross-origin)` |
| 8 | reload without sandbox | BLOCKED | no | `frameElement is null (cross-origin)` |
| 9 | `window.top` navigation | BLOCKED | no | `Failed to set a named property 'href' on 'Location'` |
| 10 | popup | BLOCKED | no | `window.open` returned `null` |
| 11 | download | BLOCKED | no | click dispatched, no download event fired |
| 12 | `/api/data/undeclared_secret` | BLOCKED | **no** | broker `403` |
| 13 | `/api/me` | BLOCKED | **no** | `Published apps can only call /api/data/<dataset>[/options\|/query]` |
| 14 | external fetch | BLOCKED | no | `Published apps cannot reach other origins` |
| 15 | `XMLHttpRequest` | BLOCKED | no | `XMLHttpRequest is not available to published apps` |
| 16 | `navigator.sendBeacon` | BLOCKED | no | returned `false` |
| 17 | `WebSocket` | BLOCKED | no | `WebSocket is not available to published apps` |
| 18 | `EventSource` | BLOCKED | no | `EventSource is not available to published apps` |
| 19 | `/api/data/undeclared_secret/query` | BLOCKED | **no** | broker `403` |
| 20 | traversal via a declared dataset | BLOCKED | no | shape refused before any fetch |

Attempts 19 and 20 were added because the broker accepts `/options` and
`/query` as sub-resources; they prove the dataset allowlist still governs them
and that a traversal segment is refused rather than normalised.

**Browser console**: no CSP violation reported in the candidate run. The
messages above are ordinary JavaScript exceptions raised by the sandbox and by
the bridge — the app is told plainly what it may not do.

**No secrets in any rejection.** The broker answers the app with a fixed
`{"detail": "request blocked by the app data broker"}`. The specific reason
(which dataset, which rule) is shown only in the wrapper's operator-facing
banner. A reason that names a dataset would turn a refusal into an enumeration
primitive. The harness planted `MUST-NEVER-BE-READ` in `/api/me` and in the
undeclared dataset; neither string appears anywhere in the app's frame.

## Positive control

A dataset the app really declared:

```
broker returned ok=true status=200
body: {"rows":[{"employee":"ok","value":42}],"scope":"tenantA/workspaceA"}
reached backend via wrapper: true
wrapper request carried the user's cookies: yes
```

The read travelled app → `postMessage` → wrapper → authenticated same-origin
fetch → `omega-app-fetch-result` → a real `Response` object in the app. The
app never held the credentials; the wrapper did.

## 18-app matrix

All 18 packaged apps, through the same wrapper and policy:

| Cartridge | Apps | Render | Broker reads | CSP violations |
|---|---|---|---|---|
| hubspot | 1 | 1 PASS | 1/1 | 0 |
| replicon | 5 | 5 PASS | 5/5 | 0 |
| salesforce | 6 | 6 PASS | 6/6 | 0 |
| sap_hcm | 2 | 2 PASS | 2/2 | 0 |
| sap_s4hana | 2 | 2 PASS | 2/2 | 0 |
| sap_successfactors | 2 | 2 PASS | 2/2 | 0 |

18/18 render, 18/18 read their declared data through the broker, **zero CSP
violations**. Workforce Overview and Talent Health both render.

External dependencies across all 18: only `cdn.jsdelivr.net` (Chart.js and
Plotly). No app references `cdn.plot.ly`, so nothing was broken by its absence
and no host was restored. Inline scripts: 1 per app, all nonce-stamped. Inline
event handlers: present in 8 apps (1–9 each) and unchanged by this work — the
policy has carried a nonce without `'unsafe-inline'` since before this PR, and
no app depends on those handlers to load data.

Two regressions this correction introduced were found by this matrix and fixed:

* `consultor_horas_costos` and `pnl_revenue_manager` read a saved theme from
  `localStorage` during bootstrap. In an opaque origin, *reading*
  `window.localStorage` throws, which aborted their inline script before it
  fetched anything. The bridge now installs a frame-local, in-memory `Storage`
  where the native one is unavailable — same API, nothing shared with the
  shell, nothing that outlives the frame.
* `consultor_horas_costos` calls `/api/data/{dataset}/options` and
  `/api/data/{dataset}/query`. An exact three-segment path check refused both.
  The broker now allows exactly those two sub-resources — the complete surface
  the data API exposes — as a closed allowlist, with the dataset still checked
  against the app's declarations.

## Authorization

`console/tests/test_published_app_authorization.py` exercises the live routes
across tenant A/workspace A, tenant A/workspace B, tenant B/workspace B, a
caller without `apps.read`, an unauthenticated caller, a hidden cartridge, a
visible-but-not-installed cartridge, an app not published for the workspace,
and declared vs undeclared datasets. It does not rely on the catalog hiding
anything: it requests the URLs directly.

## Reproducing

The harness lives outside the repository (session scratch). It needs only
`console/app/domains/apps/embed.py`; regenerate the artefacts with the real
helpers, serve them with the real headers, and drive Chromium.
