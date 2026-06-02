---
name: op-sms
description: Use when setting up, configuring, or troubleshooting OP phone numbers as a Hermes Agent SMS channel.
version: 1.0.0
author: OP + Hermes Agent contributors
license: MIT
metadata:
  hermes:
    tags: [op, sms, gateway, messaging, webhooks]
    related_skills: [hermes-agent]
---

# OP SMS for Hermes Agent

## Overview

OP lets Hermes use a phone number as an SMS channel. The `op` plugin registers a Hermes gateway platform adapter that receives OP webhooks and sends replies through `POST https://api.op.inc/v1/messages`.

Load this skill when helping someone install, configure, test, or troubleshoot OP SMS for Hermes.

## Quick Setup

Preferred dashboardless setup:

1. Install the plugin:
   ```bash
   hermes plugins install owner/hermes-op-plugin --enable
   ```

2. Run OP setup. The command sends an OTP to your phone, asks for the code, creates/uses an OP API key, optionally registers a webhook, and writes Hermes `.env` values:
   ```bash
   hermes op setup --phone +14155551234 --webhook-url https://<public-tunnel-host>/webhooks/op
   ```

3. Restart or run the gateway:
   ```bash
   hermes gateway restart
   # or
   hermes gateway run
   ```

4. Text the OP number from one of the numbers in `OP_ALLOWED_NUMBERS`.

Manual setup is still supported: add these env vars to the Hermes `.env` file shown by `hermes config env-path`:

```bash
OP_API_KEY=***
OP_WEBHOOK_SECRET=***
OP_ALLOWED_NUMBERS=+14155551234
OP_HOME_CHANNEL=+14155551234
OP_WEBHOOK_HOST=127.0.0.1
OP_WEBHOOK_PORT=8645
OP_WEBHOOK_PATH=/webhooks/op
```

## Security Defaults

- `OP_WEBHOOK_SECRET` is required unless explicit loopback-only insecure dev mode is enabled.
- Keep `OP_ALLOWED_NUMBERS` set. Do not use `OP_ALLOW_ALL_NUMBERS=true` unless you intentionally want any sender to reach Hermes.
- Do not use `OP_INSECURE_NO_SIGNATURE=true` with public tunnels or internet-facing hosts.
- Restart the gateway after changing env vars.

## Troubleshooting

| Symptom | Checks |
|---|---|
| Plugin does not show up | Run `hermes plugins list`; verify it is enabled; restart Hermes/gateway. |
| Adapter refuses to start | Check `OP_API_KEY`, `OP_WEBHOOK_SECRET`, and `OP_WEBHOOK_PORT`. |
| Webhook returns 403 | Signature secret mismatch or system clock skew >300 seconds. |
| Webhook returns 400 | OP sent invalid JSON or payload was transformed before Hermes received it. |
| Hermes ignores SMS | Sender is not in `OP_ALLOWED_NUMBERS`, or webhook is an outbound status event. |
| No SMS reply | Check OP API key, account/number permissions, and gateway logs. |
| Cron `deliver=op` fails | Set `OP_HOME_CHANNEL`; make sure cron process has OP env vars. |

## Verification Checklist

- [ ] `hermes plugins list` shows `op` enabled.
- [ ] Gateway logs show OP webhook server listening on `127.0.0.1:8645/webhooks/op`.
- [ ] `/health` returns `{"ok": true, "platform": "op"}`.
- [ ] OP test webhook reaches Hermes and signature verifies.
- [ ] Authorized inbound SMS creates a Hermes response.
- [ ] Unauthorized phone number is ignored.
- [ ] `OP_HOME_CHANNEL` works for cron/home delivery.
