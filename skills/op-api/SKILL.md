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

### Runtime SMS tools

- `op_send_sms` — send one SMS. Requires `to` and `body`; optional `idempotency_key`.
- `op_list_messages` — list messages with optional `direction`, `cursor`, `limit`.
- `op_get_message` — inspect one message/status by id.
- `op_get_number` — inspect the OP number attached to the configured runtime API key.

### Number management tools

- `op_list_my_numbers` — list numbers owned by the current OP account.
- `op_list_available_numbers` — list numbers available to lease.
- `op_lease_number` — lease an available number by `number_id`.
- `op_release_number` — release a number; destructive and requires `confirm=true`.

### API-key management tools

- `op_list_api_keys` — list OP API keys, optionally filtered by `number_id`.
- `op_create_api_key` — create an API key for a number. Treat returned key material as secret.
- `op_revoke_api_key` — revoke an API key; destructive and requires `confirm=true`.

### Webhook management tools

- `op_list_webhooks` — list OP webhooks, optionally filtered by `number_id`.
- `op_create_webhook` — create a webhook for a public HTTPS Hermes OP webhook URL.
- `op_update_webhook` — update webhook URL, events, or disabled flag.
- `op_test_webhook` — ask OP to send a test delivery to a webhook.
- `op_rotate_webhook_secret` — rotate the webhook HMAC secret; requires `confirm=true` and then updating `OP_WEBHOOK_SECRET`.
- `op_delete_webhook` — delete a webhook; destructive and requires `confirm=true`.

For destructive tools, only pass `confirm=true` after the user explicitly asked for that destructive action.

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
