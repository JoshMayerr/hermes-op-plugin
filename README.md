# Hermes OP SMS Plugin

Use an [OP](https://op.inc/) phone number as a Hermes Agent messaging channel.

This repo is a standalone Hermes plugin. It registers platform `op` so Hermes can:

- receive inbound SMS from OP webhooks
- reply over SMS with `POST https://api.op.inc/v1/messages`
- use OP as a home/cron delivery target via `OP_HOME_CHANNEL`
- load the setup/troubleshooting skill `op:op-sms`

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

## Plugin skill

This plugin ships a read-only Hermes skill:

```text
/skill op:op-sms
```

Use it for setup and troubleshooting guidance inside Hermes.

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
