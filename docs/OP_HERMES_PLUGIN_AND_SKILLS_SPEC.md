# OP Hermes Plugin + Skills Spec

This file captures the current working spec for making OP phone numbers usable as a first-class Hermes Agent channel, plus the Hermes plugin facts learned while building the initial `op` platform adapter.

Status: initial plugin implementation exists locally, tests pass, live OP end-to-end verification still needed.

## Goal

Make it as easy as possible for a Hermes user to use an OP phone number as a Hermes messaging channel.

Target user flow:

1. User gets an OP API key and phone number.
2. User installs/enables the Hermes `op` plugin or uses the bundled plugin.
3. User sets a small number of env vars.
4. User runs Hermes gateway.
5. User exposes the OP webhook endpoint with a tunnel or public host.
6. User configures the webhook in OP.
7. User texts the OP number and chats with Hermes over SMS.
8. User can use the same OP number for cron/home delivery via `deliver=op` / `OP_HOME_CHANNEL`.

## OP API facts

Source: `https://op.inc/skills.md` and session research.

### Base API

```text
Base URL: https://api.op.inc
Version prefix: /v1
Auth: Authorization: Bearer op_live_...
```

### Send SMS

```http
POST https://api.op.inc/v1/messages
Authorization: Bearer OP_API_KEY
Content-Type: application/json

{
  "to": "+14155551234",
  "body": "hello"
}
```

Expected send response shape:

```json
{
  "id": "...",
  "status": "queued",
  "from": "+1...",
  "to": "+1...",
  "body": "hello",
  "created_at": "..."
}
```

Known status lifecycle:

```text
queued -> sending -> sent | failed
```

### Message status / polling

```http
GET https://api.op.inc/v1/messages/{id}
```

Potential inbound polling fallback:

```http
GET https://api.op.inc/v1/messages?direction=inbound&limit=20
```

The initial Hermes plugin is webhook-first; polling is only reserved as a possible fallback.

### Webhooks

OP webhook signature header:

```text
op-signature: t=<unix_ts>,v1=<hex_hmac>
```

OP webhook dedupe header:

```text
op-delivery-id: <stable delivery id>
```

Signature algorithm:

```python
hmac_sha256(secret, f"{timestamp}.".encode("utf-8") + raw_body)
```

Verification requirements:

- Parse `t` and `v1` from `op-signature`.
- Reject missing/invalid signatures when `OP_WEBHOOK_SECRET` is set.
- Reject timestamps with more than 300 seconds of clock skew.
- Compare expected digest with `hmac.compare_digest`.
- Use the raw request body bytes exactly as received.
- OP signs timestamp + raw body, not the public URL, so the plugin does not need to know the ngrok/cloudflared URL to verify signatures.

Payload shapes documented/expected are not fully locked down, so the adapter should accept plausible forms:

```json
{"id":"...","direction":"inbound","from":"+1...","to":"+1...","body":"hi","created_at":"..."}
```

```json
{"type":"message.inbound","message":{...}}
```

```json
{"event":"message.inbound","data":{...}}
```

```json
{"data":{"message":{...}}}
```

Only inbound text messages should become Hermes `MessageEvent`s. Outbound status/update webhooks should be acknowledged and ignored.

## Hermes plugin architecture facts

### Plugin directory shape

Recommended OP plugin shape:

```text
plugins/op/
  plugin.yaml
  __init__.py
  adapter.py
  op_client.py
  README.md
```

Initial implementation currently has:

```text
plugins/op/README.md
plugins/op/__init__.py
plugins/op/adapter.py
plugins/op/op_client.py
plugins/op/plugin.yaml
tests/plugins/test_op_plugin.py
```

Use platform name `op` and label `OP`. Do not call the platform `op-sms` unless the product direction changes; the desired channel name is OP.

### plugin.yaml shape

`plugin.yaml` declares metadata and env prompts used by Hermes plugin/config UI. OP version:

```yaml
name: op
label: OP
kind: platform
version: 0.1.0
description: >
  OP SMS gateway adapter for Hermes Agent. Uses OP phone numbers as a Hermes
  SMS channel for inbound chat, replies, cron notifications, and simple alerts.
author: Nous Research
requires_env:
  - name: OP_API_KEY
    description: "OP API bearer token (typically starts with op_live_)"
    prompt: "OP API key"
    password: true
optional_env:
  - name: OP_WEBHOOK_SECRET
    description: "OP webhook HMAC secret; strongly recommended and required unless using loopback-only insecure dev mode"
    prompt: "OP webhook secret"
    password: true
  - name: OP_WEBHOOK_HOST
    description: "Local bind host for the webhook server (default: 127.0.0.1)"
    prompt: "Webhook bind host"
    password: false
  - name: OP_WEBHOOK_PORT
    description: "Local webhook port (default: 8645)"
    prompt: "Webhook port"
    password: false
  - name: OP_WEBHOOK_PATH
    description: "Webhook path to configure at OP (default: /webhooks/op)"
    prompt: "Webhook path"
    password: false
  - name: OP_ALLOWED_NUMBERS
    description: "Comma-separated E.164 phone numbers allowed to chat with Hermes"
    prompt: "Allowed phone numbers"
    password: false
  - name: OP_ALLOW_ALL_NUMBERS
    description: "Allow all inbound senders (not recommended; default false)"
    prompt: "Allow all numbers? (true/false)"
    password: false
  - name: OP_HOME_CHANNEL
    description: "Default E.164 phone number for cron/home delivery"
    prompt: "Home phone number"
    password: false
  - name: OP_INSECURE_NO_SIGNATURE
    description: "Disable webhook signature validation only for loopback local development"
    prompt: "Disable webhook signatures? (dev only)"
    password: false
  - name: OP_POLL_INTERVAL_SECONDS
    description: "Reserved for future inbound polling fallback"
    prompt: "Polling interval seconds"
    password: false
```

### Registration entry point

Hermes calls plugin `register(ctx)` at startup. A platform plugin registers with:

```python
def register(ctx) -> None:
    ctx.register_platform(
        name="op",
        label="OP",
        adapter_factory=lambda cfg: OPAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        required_env=["OP_API_KEY"],
        env_enablement_fn=_env_enablement,
        cron_deliver_env_var="OP_HOME_CHANNEL",
        allowed_users_env="OP_ALLOWED_NUMBERS",
        allow_all_env="OP_ALLOW_ALL_NUMBERS",
        max_message_length=MAX_OP_SMS_LENGTH,
        pii_safe=True,
        platform_hint="You are chatting via SMS through OP. Keep replies concise. Avoid markdown-heavy formatting.",
        emoji="📱",
        standalone_sender_fn=_standalone_send,
    )
```

### Platform registry fields

Hermes stores plugin platform metadata in `gateway/platform_registry.py::PlatformEntry`.

Important fields:

- `name`: config/platform identifier, e.g. `op`.
- `label`: human-readable display label, e.g. `OP`.
- `adapter_factory`: receives `PlatformConfig`, returns adapter instance.
- `check_fn`: returns whether dependencies/env are present.
- `validate_config`: optional validation before adapter creation.
- `is_connected`: optional custom connected/enabled status.
- `required_env`: env names surfaced in setup/config UI.
- `install_hint`: shown if requirements fail.
- `setup_fn`: optional interactive setup routine.
- `source`: `plugin` for plugin entries.
- `plugin_name`: manifest that registered this entry.
- `allowed_users_env`: env var for allowed IDs/phone numbers.
- `allow_all_env`: env var that bypasses allowlist if truthy.
- `max_message_length`: chunking limit.
- `pii_safe`: whether session descriptions can redact PII.
- `emoji`: display icon.
- `allow_update_command`: whether `/update` is allowed from platform.
- `platform_hint`: text injected into system prompt for platform-specific guidance.
- `env_enablement_fn`: optional env-to-`PlatformConfig.extra` function for auto-enabling/status.
- `cron_deliver_env_var`: env var used for home delivery, e.g. `OP_HOME_CHANNEL`.
- `standalone_sender_fn`: async sender usable by cron/send_message when no live gateway adapter exists.

The gateway looks up plugin adapters first with `platform_registry.create_adapter(name, config)`, then falls through to built-in legacy adapters if no plugin is registered.

### Webhook ingress pattern

Important: `ctx.register_platform(...)` registers adapter metadata only. There is no first-class shared `ctx.register_webhook_route()` / `ctx.register_gateway_route()` API for platform plugins.

For webhook-based platform plugins, self-host an `aiohttp.web.Application` inside the adapter's `connect()` method and clean it up in `disconnect()`.

Correct pattern:

```python
from aiohttp import web

class MyAdapter(BasePlatformAdapter):
    async def connect(self) -> bool:
        app = web.Application()
        app.router.add_post("/webhooks/my-platform", self._handle_webhook)
        app.router.add_get("/health", lambda _: web.json_response({"ok": True}))
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._host, self._port)
        await site.start()
        self._running = True
        return True

    async def disconnect(self) -> None:
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        self._running = False

    async def _handle_webhook(self, request):
        raw = await request.read()
        # verify signature, parse payload, authorize sender
        event = MessageEvent(...)
        await self.handle_message(event)
        return web.json_response({"success": True})
```

Built-in examples worth inspecting:

- `gateway/platforms/sms.py` — Twilio SMS webhook server.
- `gateway/platforms/bluebubbles.py` — iMessage webhook server.
- `gateway/platforms/feishu.py` — Feishu callback server.
- `gateway/platforms/webhook.py` — generic webhook platform with dynamic routes.
- `plugins/platforms/line/adapter.py` — plugin platform with webhook patterns.

Wrong surface: dashboard plugin FastAPI routes under `hermes_cli/web_server.py` / `/api/plugins/<name>/` are for the web UI/dashboard, not messaging gateway ingress.

## OP adapter design

### Core modules

`op_client.py` should contain pure helpers and HTTP client code:

- `OP_API_BASE = "https://api.op.inc"`
- `OP_API_VERSION = "/v1"`
- `MAX_OP_SMS_LENGTH = 1600`
- `SIGNATURE_TOLERANCE_SECONDS = 300`
- `parse_signature_header(header) -> ParsedSignature | None`
- `compute_webhook_signature(secret, timestamp, raw_body) -> str`
- `verify_webhook_signature(secret, signature_header, raw_body, now=None, tolerance_seconds=300) -> bool`
- `format_op_error(status, body) -> str`
- `OPClient.send_message(to, body) -> tuple[int, Any]`
- `OPClient.get_message(message_id) -> tuple[int, Any]`

`adapter.py` should contain:

- env parsing helpers (`_truthy`, `_split_csv`, `_parse_webhook_port`, `_normalize_webhook_path`)
- `check_requirements()`
- `validate_config()`
- `_env_enablement()`
- `_standalone_send()`
- `OPAdapter(BasePlatformAdapter)`
- `register(ctx)`

### Default env vars

Required:

```bash
OP_API_KEY=op_live_...
```

Strongly recommended / effectively required for non-dev:

```bash
OP_WEBHOOK_SECRET=...
OP_ALLOWED_NUMBERS=+14155551234,+14155559876
```

Optional:

```bash
OP_WEBHOOK_HOST=127.0.0.1
OP_WEBHOOK_PORT=8645
OP_WEBHOOK_PATH=/webhooks/op
OP_ALLOW_ALL_NUMBERS=false
OP_HOME_CHANNEL=+14155551234
OP_INSECURE_NO_SIGNATURE=false
OP_PHONE_NUMBER=+1...        # own OP number, used to ignore echoes if needed
OP_POLL_INTERVAL_SECONDS=... # reserved for future polling fallback
```

### Secure defaults

The OP SMS adapter should default to strict behavior:

- Bind webhook listener to `127.0.0.1` by default so local tunnel workflows are safe.
- Require `OP_WEBHOOK_SECRET` unless explicit insecure local dev mode is enabled.
- Only allow insecure no-signature mode when `OP_INSECURE_NO_SIGNATURE=true` and bind host is loopback-only.
- Warn clearly that insecure mode must not be used with cloudflared/ngrok/public tunnels, even if the local bind address is loopback.
- Require sender allowlist by default via `OP_ALLOWED_NUMBERS`.
- Only allow open inbound SMS if `OP_ALLOW_ALL_NUMBERS=true`.
- Do not log plaintext SMS bodies at info level.
- Redact phone numbers in logs with `gateway.platforms.helpers.redact_phone`.
- Dedupe webhooks by both `op-delivery-id` and stable message id fallback.
- Parse env values safely; malformed env should not crash plugin discovery.
- Reject invalid webhook ports early.
- Clean up aiohttp `AppRunner` and client session on startup failure.

### Webhook server defaults

```text
Host: 127.0.0.1
Port: 8645
Path: /webhooks/op
Health: /health
```

Health response:

```json
{"ok": true, "platform": "op"}
```

Tunnel setup example:

```bash
OP_WEBHOOK_HOST=127.0.0.1
OP_WEBHOOK_PORT=8645
cloudflared tunnel --url http://localhost:8645
# Configure OP webhook URL: https://<trycloudflare>/webhooks/op
```

Because OP signs timestamp + raw body, not URL, signature validation should survive tunnels without knowing the public URL.

### Sending behavior

- Strip markdown before SMS send with `strip_markdown`.
- Chunk long messages using `BasePlatformAdapter.truncate_message(..., max_length=MAX_OP_SMS_LENGTH)`.
- Use `POST /v1/messages` for each chunk.
- Return the last message id as `message_id` and earlier chunk ids as `continuation_message_ids`.
- Mark retryable on transient API statuses: `429`, `500`, `502`, `503`, `504`.
- Reject media attachments because OP SMS currently sends text only.

### Inbound behavior

On webhook:

1. Read raw body.
2. Read `op-delivery-id` header.
3. If `OP_WEBHOOK_SECRET` is set, verify `op-signature`.
4. If signature invalid/stale, return `403`.
5. Parse JSON. If invalid, return `400`.
6. Extract inbound message from plausible payload envelope.
7. Ignore non-inbound/status events with success response.
8. Dedupe by delivery id and message id.
9. Ignore own-number echo if `OP_PHONE_NUMBER` matches sender.
10. Enforce allowlist.
11. Build Hermes source:
    - `chat_id = from_number`
    - `chat_name = from_number`
    - `chat_type = "dm"`
    - `user_id = from_number`
    - `user_name = from_number`
    - `message_id = OP message id or delivery id`
12. Create `MessageEvent` with `MessageType.TEXT`.
13. Dispatch `self.handle_message(event)` as a background task.
14. Return `{"success": true}`.

Unauthorized senders should generally be acknowledged and ignored rather than returning an error that could cause provider retries:

```json
{"success": true, "ignored": true, "reason": "unauthorized"}
```

## Hermes skill plan

The plugin alone is not enough for the “easy to start using OP phone numbers” goal. Add bundled skills/docs that encode setup and troubleshooting.

Recommended skill location:

```text
skills/productivity/op-sms/SKILL.md
```

Possible name:

```yaml
name: op-sms
```

Use this naming for the skill if it is specifically about SMS setup, while keeping the platform/plugin name `op`.

Skill frontmatter should follow in-repo skill conventions:

```yaml
---
name: op-sms
description: Use when setting up, configuring, or troubleshooting OP phone numbers as a Hermes Agent SMS channel.
version: 1.0.0
author: Hermes Agent
license: MIT
metadata:
  hermes:
    tags: [op, sms, gateway, messaging, webhooks]
    related_skills: [hermes-agent]
---
```

Skill content should include:

- Overview: OP phone numbers as Hermes SMS channel.
- Prerequisites:
  - OP account/API key.
  - OP phone number.
  - Hermes Agent installed.
  - Gateway enabled.
  - Tunnel or public HTTPS endpoint for webhook.
- Minimal `.env` template.
- Local tunnel recipe with cloudflared.
- ngrok alternative.
- Gateway setup/run commands.
- OP dashboard webhook configuration.
- First inbound SMS test.
- Outbound reply test.
- Cron/home delivery setup with `OP_HOME_CHANNEL`.
- Security recommendations.
- Troubleshooting table.
- Verification checklist.

### Skill setup checklist draft

```bash
# 1. Add secrets/config to Hermes env
hermes config env-path
# edit ~/.hermes/.env or profile-specific .env:
OP_API_KEY=op_live_...
OP_WEBHOOK_SECRET=...
OP_ALLOWED_NUMBERS=+14155551234
OP_HOME_CHANNEL=+14155551234
OP_WEBHOOK_HOST=127.0.0.1
OP_WEBHOOK_PORT=8645
OP_WEBHOOK_PATH=/webhooks/op

# 2. Start gateway
hermes gateway run

# 3. In a second terminal, expose webhook
cloudflared tunnel --url http://localhost:8645

# 4. Configure OP webhook URL
# https://<public-tunnel-host>/webhooks/op

# 5. Text the OP number from an allowed phone number
```

### Skill troubleshooting topics

- Gateway does not show OP platform.
  - Check plugin enabled / bundled plugin discovery.
  - Check `OP_API_KEY` is set.
  - Restart gateway after env changes.
- Adapter refuses to start.
  - Missing `OP_WEBHOOK_SECRET`.
  - Bad `OP_WEBHOOK_PORT`.
  - `OP_INSECURE_NO_SIGNATURE` used with non-loopback host.
- OP webhook gets 403.
  - Signature secret mismatch.
  - Raw body changed by proxy (should not happen with standard tunnels).
  - System clock skew >300s.
- OP webhook gets 400.
  - Invalid JSON payload.
- Hermes ignores inbound message.
  - Sender not in `OP_ALLOWED_NUMBERS`.
  - Event is outbound status update, not inbound message.
  - Payload shape differs; inspect logs/raw OP test delivery.
- User receives no SMS reply.
  - OP API key invalid.
  - OP account/number not authorized for destination.
  - Long reply chunking failed on later chunk.
  - Check gateway logs for redacted send errors.
- Cron delivery fails.
  - Missing `OP_HOME_CHANNEL`.
  - Missing `standalone_sender_fn` registration.
  - Cron process lacks OP env vars.

## Docs plan

Add official user-facing docs under the messaging integrations docs, likely:

```text
website/docs/user-guide/messaging/op.md
```

Docs should be less developer-oriented than plugin README and should include:

- What OP is.
- Why use OP with Hermes.
- Prerequisites.
- Step-by-step setup.
- Environment variable table.
- Tunnel setup.
- OP dashboard webhook setup.
- Testing inbound/outbound.
- Security notes.
- Troubleshooting.
- Cron delivery examples.

Potential README/docs distinction:

- `plugins/op/README.md`: developer/plugin-specific reference.
- `website/docs/user-guide/messaging/op.md`: end-user setup guide.
- `skills/productivity/op-sms/SKILL.md`: agent-operational procedure for helping users configure/troubleshoot.

## Test spec

Current focused tests pass:

```bash
venv/bin/python -m pytest -q tests/plugins/test_op_plugin.py
# 12 passed
```

Focused test coverage should include:

- Signature header parsing.
- Signature verification success.
- Stale timestamp rejection.
- Missing/invalid signature rejection.
- Error formatting.
- Payload extraction for direct object and common envelope shapes.
- Non-inbound/status events ignored.
- Sender allowlist enforcement.
- Deduplication by `op-delivery-id`.
- Deduplication by message id fallback.
- Invalid `OP_WEBHOOK_PORT` validation.
- No plaintext SMS body in info logs.
- Standalone sender rejects media.
- Long SMS chunking behavior.
- Plugin registration in `platform_registry`.

Suggested commands:

```bash
# Focused OP tests
venv/bin/python -m pytest -q tests/plugins/test_op_plugin.py

# Targeted platform/gateway area
venv/bin/python -m pytest -q tests/plugins tests/gateway -o 'addopts='

# Full suite before PR
venv/bin/python -m pytest tests/ -o 'addopts=' -q
```

## Live verification checklist

Before shipping, run a real OP E2E test:

- [ ] Real `OP_API_KEY` can send SMS via `POST /v1/messages`.
- [ ] Real OP webhook delivery reaches local Hermes gateway through tunnel.
- [ ] Real `op-signature` header matches documented `t=...,v1=...` format.
- [ ] Real signature verifies with `timestamp + "." + raw_body`.
- [ ] Real webhook includes `op-delivery-id`, or adapter works without it.
- [ ] Real inbound payload matches one of adapter’s accepted shapes, or adapter updated.
- [ ] Outbound status webhook is ignored without triggering Hermes.
- [ ] Unauthorized sender is ignored.
- [ ] Authorized sender gets Hermes response by SMS.
- [ ] `OP_HOME_CHANNEL` works for `send_message`/cron-style standalone delivery.
- [ ] Gateway restart picks up env/plugin cleanly.

## Current local implementation status

Files currently added locally under `/Users/joshmayer/.hermes/hermes-agent`:

```text
plugins/op/README.md
plugins/op/__init__.py
plugins/op/adapter.py
plugins/op/op_client.py
plugins/op/plugin.yaml
tests/plugins/test_op_plugin.py
```

Current tested behavior:

- plugin registration works under name `op` / label `OP`.
- send API uses `POST https://api.op.inc/v1/messages`.
- webhook listener defaults to `127.0.0.1:8645/webhooks/op`.
- health route at `/health`.
- signature validation implemented.
- replay window is 300s.
- dedupe implemented by delivery id and message id fallback.
- allowlist enforced by default.
- insecure no-signature mode blocked unless loopback-only.
- markdown stripped and SMS replies chunked.
- `OP_HOME_CHANNEL` and `standalone_sender_fn` supported.
- info logs avoid plaintext SMS body.

## Open questions / product decisions

- Should OP webhook secret be absolutely required always, with no insecure dev mode?
- Should the plugin offer polling fallback for people who cannot expose a webhook?
- Should Hermes gateway setup generate a webhook secret for the user?
- Should OP setup be part of `hermes gateway setup` with a guided wizard?
- Should allowed numbers default to `OP_HOME_CHANNEL` if `OP_ALLOWED_NUMBERS` is omitted?
- Should the OP plugin support MMS/media if OP adds that API?
- Should there be a `hermes op test` style tool/command, or just docs/skill?
- Should the plugin expose OP message status lookup as a Hermes tool, or keep it as gateway-only?

## PR packaging checklist

- [ ] Verify live OP API/webhook behavior.
- [ ] Add/adjust tests based on real payloads.
- [ ] Add bundled skill under `skills/productivity/op-sms/SKILL.md`.
- [ ] Add docs page under `website/docs/user-guide/messaging/op.md`.
- [ ] Ensure plugin name is consistently `op`.
- [ ] Search for accidental `op-sms` platform references.
- [ ] Run focused tests.
- [ ] Run relevant gateway/plugin tests.
- [ ] Run full test suite if practical.
- [ ] Commit plugin, tests, docs, and skill.
- [ ] PR description includes manual verification evidence.
