# OP plugin installed

The `op` platform plugin is installed.

Next steps:

1. Set required env vars in your Hermes `.env`:
   - `OP_API_KEY`
   - `OP_WEBHOOK_SECRET`
   - `OP_ALLOWED_NUMBERS`
   - optionally `OP_HOME_CHANNEL`
2. Enable the plugin if the installer did not enable it:
   ```bash
   hermes plugins enable op
   ```
3. Restart the gateway:
   ```bash
   hermes gateway restart
   ```
4. Expose the local webhook server:
   ```bash
   cloudflared tunnel --url http://localhost:8645
   ```
5. Configure OP webhook URL:
   ```text
   https://<your-tunnel>/webhooks/op
   ```

For full setup instructions, open this plugin's `README.md` or load the plugin skill:

```text
/skill op:op-sms
```
