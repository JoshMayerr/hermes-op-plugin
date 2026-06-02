---
name: op-api
description: Use when operating OP API tools or doing dashboardless OP setup from Hermes.
version: 1.0.0
author: OP + Hermes Agent contributors
license: MIT
metadata:
  hermes:
    tags: [op, sms, api, tools, dashboardless-setup]
    related_skills: [op-sms, hermes-agent]
---

# OP API for Hermes

## When to Use

Use this skill when the user wants Hermes to operate OP directly: send SMS, inspect messages/status, check the configured OP number, or set up OP without visiting the dashboard.

## Dashboardless Setup Model

OP supports setup without the dashboard:

1. `POST /auth/start` with the user's personal E.164 phone number.
2. User receives an OTP by SMS and gives it to the local CLI prompt.
3. `POST /auth/verify` returns `session_token`, user metadata, and often `bootstrap_key.key`.
4. Use `session_token` against `/console/*` endpoints to inspect numbers, create API keys, and create/test webhooks.
5. Save durable Hermes runtime values in `.env`:
   - `OP_API_KEY`
   - `OP_WEBHOOK_SECRET`
   - `OP_ALLOWED_NUMBERS`
   - `OP_HOME_CHANNEL`
   - `OP_PHONE_NUMBER`
   - `OP_WEBHOOK_HOST`, `OP_WEBHOOK_PORT`, `OP_WEBHOOK_PATH`

The user must still provide the OTP. Do not claim fully autonomous signup without human OTP possession.

## CLI Commands

```bash
hermes op setup
hermes op setup --phone +14155551234 --webhook-url https://example.com/webhooks/op
hermes op status
```

After setup, restart Hermes/gateway so env vars reload.

## OP Tools

The plugin registers toolset `op` when `OP_API_KEY` is set:

- `op_send_sms` — send one SMS. Requires `to` and `body`; optional `idempotency_key`.
- `op_list_messages` — list messages with optional `direction`, `cursor`, `limit`.
- `op_get_message` — inspect one message/status by id.
- `op_get_number` — inspect the OP number attached to the configured API key.

## Operational Rules

- Use E.164 numbers (`+14155551234`).
- Keep SMS concise; OP max body length is 1600 chars.
- Use `idempotency_key` for retries or automation.
- On webhook handling, verify `op-signature` with HMAC-SHA256 over `timestamp + "." + raw_body`.
- Deduplicate webhook deliveries with `op-delivery-id` and message id fallback.
- Respect rate limits. On 429, use `Retry-After`.

## Common Errors

| Code | Meaning |
|---|---|
| `invalid_phone` | Phone is not E.164. |
| `verification_invalid` | OTP is wrong or attempts exhausted. |
| `verification_expired` | OTP is older than 10 minutes. |
| `self_send` | Recipient equals the OP sending number. |
| `body_too_long` | SMS exceeds 1600 chars. |
| `unauthenticated` | API key/session missing, expired, or revoked. |
| `rate_limited` | Wait for `Retry-After`. |
| `free_tier_limit_reached` | Free daily SMS cap exhausted. |
