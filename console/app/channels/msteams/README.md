# Microsoft Teams channel

A **transport-only** channel that lets the Copiloto Empresarial live inside
Microsoft Teams. Teams receives/sends messages; **all business logic stays in
the copilot and cartridges**. This package never decides anything beyond
"who is allowed" and "translate Teams ↔ internal contract".

```
Teams  →  POST /api/msteams/messages  (router: app/routers/msteams.py)
       →  security.authorize / verify_jwt        (security.py)
       →  adapter.parse_activity → InternalCopilotRequest   (adapter.py)
       →  service.handle_activity                (service.py)
       →  copilot_service.run_turn  ← THE EXISTING COPILOT
       →  adapter.from_copilot_response → Teams activity
       →  HTTP 200 reply  +  audit_service.record_event
```

## Capability levels

| Level | What | Status |
|---|---|---|
| 0 | Disabled (`MSTEAMS_ENABLED=false`) — ignores everything | ✅ |
| 1 | Basic bot — allowlisted users DM the copilot, get a reply; mentions in channels | ✅ |
| 2 | Allowlisted conversations — admin scopes users/teams/channels | ✅ |
| 3 | Post-meeting — read transcripts, attendance, agreements/tasks/risks | 🚧 scaffolded, OFF, fails closed |
| 4 | Advanced — files, SharePoint/OneDrive, adaptive cards, proactive alerts | 🔌 interfaces only |

## Environment

All variables are documented in `infra/.env.example`. The essentials:

| Var | Default | Meaning |
|---|---|---|
| `MSTEAMS_ENABLED` | `false` | Master switch. False ⇒ webhook ignores everything. |
| `MSTEAMS_APP_ID` / `MSTEAMS_APP_PASSWORD` / `MSTEAMS_TENANT_ID` | – | Azure Bot credentials. |
| `MSTEAMS_WEBHOOK_PATH` | `/api/msteams/messages` | Keep aligned with the Azure Bot endpoint. |
| `MSTEAMS_JWT_VALIDATION` | `claims` | `claims` \| `strict` (fail-closed) \| `disabled`. |
| `MSTEAMS_DM_POLICY` / `MSTEAMS_GROUP_POLICY` | `allowlist` | `allowlist` \| `open` \| `disabled`. |
| `MSTEAMS_REQUIRE_MENTION` | `true` | Channels/groups only act when the bot is @mentioned. |
| `MSTEAMS_ALLOWED_USERS` / `_CONVERSATIONS` / `_TENANTS` | empty | Stable-id allowlists. **Empty + allowlist policy = nobody.** |
| `MSTEAMS_USER_MAP` | empty | `aadObjectId=email,…` → console user. |
| `MSTEAMS_DEFAULT_USER_EMAIL` | empty | Fallback console service account for allowed senders. |
| `MSTEAMS_GRAPH_ENABLED` / `MSTEAMS_TRANSCRIPTS_ENABLED` | `false` | Level 3 gates. |
| `MSTEAMS_SHAREPOINT_SITE_ID` | empty | Level-4 scaffold (files/SharePoint). Unused in v0.1. |

The admin `GET /api/msteams/status` endpoint requires a console session AND
the `settings.read` permission (admin / owner / security_admin /
workspace_admin roles). Operators without it get a generic 403; the
response never contains secrets (only counts / booleans).

## Local setup & testing

1. **Configure env** (at minimum, in `infra/.env`):
   ```
   MSTEAMS_ENABLED=true
   MSTEAMS_APP_ID=<azure-bot-app-id>
   MSTEAMS_APP_PASSWORD=<client-secret>
   MSTEAMS_TENANT_ID=<tenant-guid>
   MSTEAMS_DM_POLICY=allowlist
   MSTEAMS_ALLOWED_USERS=<your-aad-object-id>
   MSTEAMS_USER_MAP=<your-aad-object-id>=you@your-org.com
   ```
2. **Create the bot/app in Azure + Teams.** Use the Teams toolkit/CLI or the
   Azure portal: create an Entra ID app + client secret, register an Azure
   Bot, and build a Teams app manifest from `manifest.template.json`
   (replace every `REPLACE_WITH_*` placeholder; **no real ids in git**).
3. **Expose the endpoint publicly** (Teams can't reach `localhost`):
   ```
   devtunnel create my-copilot-bot --allow-anonymous
   devtunnel port create my-copilot-bot -p 8000 --protocol auto
   devtunnel host my-copilot-bot
   # Bot messaging endpoint: https://<tunnel>/api/msteams/messages
   ```
   (`ngrok http 8000` works too.) Set the Azure Bot "Messaging endpoint" to
   that URL.
4. **Install the app in Teams** (upload the custom app / org app catalog).
5. **Test a direct message**: DM the bot. An allowlisted+mapped sender gets a
   copilot reply; a non-allowlisted sender is silently ignored.
6. **Test a channel mention**: add the bot to an allowlisted channel and
   `@mention` it. Without a mention it stays silent (default).
7. **Graph/transcripts** require Microsoft 365 / Entra **admin consent** and
   the tenant having transcription enabled. If the company doesn't enable
   transcription, **there is no transcript to read** — Level 3 stays closed.

## Security model

- **Default-closed**: disabled channel, allowlist policies, name matching off.
- **Stable ids only** for authorization (AAD object id, Bot Framework
  conversation id). Display names are mutable and never authorize anything;
  there is no name-matching escape hatch in this version.
- **Empty allowlist + allowlist policy = nobody** (never "everybody").
- **Secrets**: never logged, never returned by `/status`, never in audit.
- **Errors**: contained — the webhook returns a generic reply and a safe
  audit `error_type`; no stacktraces/paths/secrets leave the process.
- **Audit**: every interaction → `audit_service.record_event(action="msteams.message")`
  with tenant/user/conversation/mode/status/error_type and message **length**
  (never the raw text — privacy).

### Identity and tenant model — read before deploying

- **Prefer AAD object ids** in `MSTEAMS_ALLOWED_USERS` and `MSTEAMS_USER_MAP`.
  Bot Framework's `from.id` (`29:…`) is per-bot-conversation and changes
  between bot apps; AAD object ids are globally unique per user.
- **B2B guests**: a guest's `aadObjectId` is the **home-tenant** GUID, not the
  inviting tenant's. Map guests explicitly in `MSTEAMS_USER_MAP`.
- **Empty `MSTEAMS_ALLOWED_TENANTS`** does NOT mean "nobody" — it means "all
  tenants allowed by tenant gate". The bot is still gated by `MSTEAMS_USER_MAP`
  (AAD GUIDs are globally unique, so user map is itself a global allowlist).
- **`MSTEAMS_DEFAULT_USER_EMAIL` in group/open mode**: every channel member
  resolves to the same console identity. All audit + history is recorded
  under that one user.
- **`MSTEAMS_REQUIRE_MENTION=false` + `MSTEAMS_GROUP_POLICY=open`** is the
  "fully open in the channel" mode: any tenant member triggers the bot
  without `@`. Combine intentionally.
- **Public Microsoft cloud only**: claims-mode JWT validates against the
  public Bot Framework issuers
  (`https://api.botframework.com` / `https://login.botframework.com`).
  Government / sovereign clouds (`*.botframework.us`, `*.botframework.cn`)
  are NOT supported in this version.

### Activity types we acknowledge silently

Teams sends several activity types this channel does NOT act on in v0.1.
They get a 200-empty response so Teams won't retry, and they NEVER reach
the copilot. No welcome / typing UX yet — Level-1 completion items:

| Activity | Behaviour | Future |
|---|---|---|
| `conversationUpdate` (bot installed in a chat) | Ignored | Welcome card (Level-1) |
| `typing` | Ignored | Send typing back (Level-1) |
| `messageReaction` | Ignored | Audit/react (Level-2) |
| `meeting`-context messages | Routed through group policy; transcript ingestion fails closed | Post-meeting ingestion (Level-3) |

### Reply threading (Level-1 completion item)

The current inline-reply path writes the activity body in the webhook
response. Teams renders this correctly in personal DMs, less well in
channel threads (the reply may post as a top-level message instead of
threading on the original). The Bot Connector "reply to activity" proactive
call against the stored `serviceUrl` is the production fix.

### JWT signature verification (hardening item)

`claims` mode validates issuer/audience/tenant/expiry of the Bot Framework
JWT **without verifying the signature**. That is an honest, documented
limitation suitable for a disabled-by-default Level-1 bot behind a tunnel.
Before production, set `MSTEAMS_JWT_VALIDATION=strict` — which currently
**fails closed** because the JWKS signature verifier is not yet wired
(`security._signature_verifier` returns `None`). Wiring full Bot Framework
JWKS verification there is the next hardening PR.

### Reply delivery (hardening item)

v0.1 returns the reply activity in the webhook HTTP response (covers
inline-reply hosts + tests). Production-grade delivery to every Teams surface
uses the Bot Connector "reply to activity" call against the stored
`serviceUrl`; that proactive sender is a documented Level-1 completion item.

## Files

| File | Responsibility |
|---|---|
| `config.py` | Env reading, levels, allowlist parsing. |
| `schemas.py` | `InboundTeamsEvent`, `InternalCopilotRequest/Response`, `ChannelResult`. |
| `security.py` | Enabled/JWT/tenant/dm/group gates; console-user resolution. |
| `adapter.py` | Teams activity ↔ internal contract (pure). |
| `service.py` | Orchestration → `copilot_service.run_turn` + audit. |
| `meetings.py` | Level-3 post-meeting scaffolding (fail-closed). |
| `manifest.template.json` | Teams app manifest (placeholders only). |
| `../../routers/msteams.py` | Webhook + admin `/status` endpoints. |
| `../../../infra/init/95_msteams_channel.sql` | Conversation mapping table. |
