# Hermes OP SMS Plugin

Use an [OP](https://op.inc/) phone number as a Hermes Agent messaging channel.

This repo is a standalone Hermes plugin. It registers platform `op` so Hermes can:

- receive inbound SMS from OP webhooks
- reply over SMS with `POST https://api.op.inc/v1/messages`
- use OP as a home/cron delivery target via `OP_HOME_CHANNEL`
- run dashboardless OTP setup with `hermes op setup`
- expose OP API tools (`op_send_sms`, `op_list_messages`, `op_get_message`, `op_get_number`)
- load the setup/troubleshooting skills `op:op-sms` and `op:op-api`

## Install

Once this repo is on GitHub:

```bash
hermes plugins install <owner>/<repo> --enable
# example:
# hermes plugins install op-inc/hermes-op-plugin --enable
```

Or install from a full Git URL:

```bash
hermes plugins install https://github.com/<owner>/<repo>.git --enable
```

Restart the gateway after install:

```bash
hermes gateway restart
```

## Dashboardless setup

OP supports signup/login by OTP, so the plugin can configure most of OP without opening the dashboard:

```bash
hermes op setup --phone +14155551234 --webhook-url https://<your-public-tunnel>/webhooks/op
```

The command will:

1. call `POST /auth/start` for your phone number
2. prompt for the OTP OP texts you
3. call `POST /auth/verify`
4. use the returned `bootstrap_key` or create an API key through `/console/api-keys`
5. inspect your OP number through `/console/numbers/mine`
6. create a webhook through `/console/webhooks` when `--webhook-url` is provided
7. write the needed `OP_*` values to the Hermes `.env`

Then restart Hermes/gateway:

```bash
hermes gateway restart
```

You can inspect saved values without revealing secrets:

```bash
hermes op status
```

## Configuration

Find your Hermes env file:

```bash
hermes config env-path
```

Add:

```bash
OP_API_KEY=op_live_...
OP_WEBHOOK_SECRET=...
OP_ALLOWED_NUMBERS=+14155551234
OP_HOME_CHANNEL=+14155551234
OP_WEBHOOK_HOST=127.0.0.1
OP_WEBHOOK_PORT=8645
OP_WEBHOOK_PATH=/webhooks/op
```

Required:

| Env var | Purpose |
|---|---|
| `OP_API_KEY` | OP bearer token. |

Strongly recommended:

| Env var | Purpose |
|---|---|
| `OP_WEBHOOK_SECRET` | HMAC secret for OP webhook signature verification. Required unless local insecure dev mode is explicitly enabled. |
| `OP_ALLOWED_NUMBERS` | Comma-separated E.164 phone numbers allowed to chat with Hermes. |

Optional:

| Env var | Default | Purpose |
|---|---:|---|
| `OP_WEBHOOK_HOST` | `127.0.0.1` | Local bind host for the webhook server. |
| `OP_WEBHOOK_PORT` | `8645` | Local webhook port. |
| `OP_WEBHOOK_PATH` | `/webhooks/op` | Path to configure in OP. |
| `OP_HOME_CHANNEL` | unset | Default SMS destination for cron/home delivery. |
| `OP_ALLOW_ALL_NUMBERS` | `false` | Allow any inbound sender. Not recommended. |
| `OP_INSECURE_NO_SIGNATURE` | `false` | Disable signature verification only for loopback local development. |
| `OP_PHONE_NUMBER` | unset | Your OP number; used to ignore echoes if needed. |

## Webhook setup

Run the gateway:

```bash
hermes gateway run
```

In another terminal, expose the local OP webhook server:

```bash
cloudflared tunnel --url http://localhost:8645
```

Configure OP's webhook URL:

```text
https://<your-public-tunnel>/webhooks/op
```

Health check:

```bash
curl http://localhost:8645/health
# {"ok": true, "platform": "op"}
```

## Security model

- OP signs webhooks with `op-signature: t=<unix_ts>,v1=<hex_hmac>`.
- The plugin verifies `HMAC_SHA256(OP_WEBHOOK_SECRET, timestamp + "." + raw_body)`.
- Webhooks older/newer than 300 seconds are rejected.
- Duplicate deliveries are ignored using `op-delivery-id` and message id fallback.
- Inbound senders are rejected unless allowlisted or `OP_ALLOW_ALL_NUMBERS=true`.
- SMS bodies are not logged at info level.

## OP API tools

When `OP_API_KEY` is set, the plugin registers toolset `op`:

| Tool | Purpose |
|---|---|
| `op_send_sms` | Send a one-off SMS with optional idempotency key. |
| `op_list_messages` | List inbound/outbound messages with pagination. |
| `op_get_message` | Fetch a message and delivery status by id. |
| `op_get_number` | Inspect the OP number attached to the API key. |

## Plugin skills

This plugin ships read-only Hermes skills:

```text
/skill op:op-sms
/skill op:op-api
```

Use them for setup, troubleshooting, and API/tool guidance inside Hermes.

## Local development

Clone this plugin repo next to a Hermes checkout. Then run tests with Hermes on `PYTHONPATH`:

```bash
cd hermes-op-plugin
PYTHONPATH=/path/to/hermes-agent python3 -m pytest -q
```

To test installation locally:

```bash
hermes plugins install file://$PWD --enable --force
hermes gateway restart
```

## Current status

Unit tests cover signature verification, payload extraction, dedupe, config validation, allowlist behavior, logging safety, and plugin registration. A real OP end-to-end webhook/send test is still recommended before broad production use.
