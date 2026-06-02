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

Install the plugin from GitHub:

```bash
hermes plugins install JoshMayerr/hermes-op-plugin --enable
```

Or install from the full Git URL:

```bash
hermes plugins install https://github.com/JoshMayerr/hermes-op-plugin.git --enable
```

Restart the gateway after install:

```bash
hermes gateway restart
```

## Dashboardless setup

OP supports signup/login by OTP, so the plugin can configure OP without opening the dashboard. The only manual step is entering the OTP that OP texts you.

### Setup order

1. **Install and enable the plugin.**

   ```bash
   hermes plugins install JoshMayerr/hermes-op-plugin --enable
   ```

2. **Get the public webhook URL OP will call.**

   Hermes listens locally on `http://localhost:8645/webhooks/op` by default. OP needs a public HTTPS URL that forwards to that local address.

   The easiest option is Cloudflare Tunnel (`cloudflared`):

   ```bash
   cloudflared tunnel --url http://localhost:8645
   ```

   Cloudflare prints a URL like:

   ```text
   https://example-random.trycloudflare.com
   ```

   Append the OP webhook path:

   ```text
   https://example-random.trycloudflare.com/webhooks/op
   ```

   That full URL is the value to pass as `--webhook-url`.

3. **Run dashboardless OP setup.** Use your personal phone number for OTP login, not necessarily the OP number.

   ```bash
   hermes op setup --phone +14155551234 --webhook-url https://example-random.trycloudflare.com/webhooks/op
   ```

   The command will:

   1. call `POST /auth/start` for your phone number
   2. prompt for the OTP OP texts you
   3. call `POST /auth/verify`
   4. use the returned `bootstrap_key` or create an API key through `/console/api-keys`
   5. inspect your OP number through `/console/numbers/mine`
   6. create/update the OP webhook through `/console/webhooks`
   7. write the needed `OP_*` values to the Hermes `.env`

4. **Restart Hermes/gateway so the new env vars load.**

   ```bash
   hermes gateway restart
   ```

5. **Verify the local webhook server.**

   ```bash
   curl http://localhost:8645/health
   # {"ok": true, "platform": "op"}
   ```

6. **Send a test SMS to your OP number.** Hermes should receive it through OP's webhook and reply over SMS.

You can inspect saved values without revealing secrets:

```bash
hermes op status
```

If you use a temporary tunnel URL, it changes whenever the tunnel restarts. Re-run `hermes op setup --webhook-url <new-url>/webhooks/op` or update the OP webhook whenever the public URL changes.

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

## Manual webhook setup

If you do not pass `--webhook-url` to `hermes op setup`, configure the webhook yourself in OP after setup.

The URL OP should call is your public HTTPS base URL plus `OP_WEBHOOK_PATH`:

```text
https://<your-public-host>/webhooks/op
```

For local development, get the public host from a tunnel:

```bash
cloudflared tunnel --url http://localhost:8645
```

Then use the printed `https://...trycloudflare.com/webhooks/op` URL in OP.

After the OP webhook is configured and the gateway is running, verify the local endpoint:

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

Unit tests cover signature verification, payload extraction, dedupe, config validation, allowlist behavior, logging safety, and plugin registration. A real OP end-to-end webhook/send test is still recommended before broad use.
