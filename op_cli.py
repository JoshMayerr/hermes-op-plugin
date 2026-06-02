"""Dashboardless OP setup and CLI helpers for Hermes."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import re
from pathlib import Path
from typing import Any, Callable, Mapping

try:
    from .adapter import DEFAULT_WEBHOOK_HOST, DEFAULT_WEBHOOK_PATH, DEFAULT_WEBHOOK_PORT
    from .op_client import OPClient, format_op_error
except ImportError:  # pragma: no cover - top-level pytest/import fallback
    from adapter import DEFAULT_WEBHOOK_HOST, DEFAULT_WEBHOOK_PATH, DEFAULT_WEBHOOK_PORT  # type: ignore
    from op_client import OPClient, format_op_error  # type: ignore

_PHONE_RE = re.compile(r"^\+[1-9]\d{6,14}$")


def redact_secret(value: str) -> str:
    """Return a compact redacted representation for setup summaries."""
    text = str(value or "")
    if len(text) <= 8:
        return "***" if text else ""
    return f"{text[:4]}…{text[-4:]}"


def normalize_phone(value: str) -> str:
    """Normalize and validate E.164-ish phone input."""
    phone = str(value or "").strip().replace(" ", "").replace("-", "")
    if not _PHONE_RE.match(phone):
        raise ValueError("Phone number must be E.164, e.g. +14155551234")
    return phone


def _first_mapping(value: Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        for key in ("data", "number", "item", "result"):
            nested = value.get(key)
            if isinstance(nested, Mapping):
                return nested
        return value
    if isinstance(value, list) and value and isinstance(value[0], Mapping):
        return value[0]
    return {}


def extract_number_payload(payload: Any) -> Mapping[str, Any]:
    """Extract the first OP number-ish object from common API response shapes."""
    if isinstance(payload, Mapping):
        for key in ("numbers", "data", "items", "results"):
            nested = payload.get(key)
            if isinstance(nested, list) and nested and isinstance(nested[0], Mapping):
                return nested[0]
        return _first_mapping(payload)
    return _first_mapping(payload)


def _payload_api_key(payload: Mapping[str, Any]) -> str:
    bootstrap = payload.get("bootstrap_key")
    if isinstance(bootstrap, Mapping) and bootstrap.get("key"):
        return str(bootstrap["key"])
    if payload.get("key"):
        return str(payload["key"])
    return ""


def _payload_phone(payload: Mapping[str, Any]) -> str:
    for key in ("phone", "number", "phone_number", "e164"):
        if payload.get(key):
            return str(payload[key])
    return ""


def _payload_id(payload: Mapping[str, Any]) -> str:
    for key in ("id", "number_id"):
        if payload.get(key):
            return str(payload[key])
    return ""


def build_env_updates(
    *,
    verify_payload: Mapping[str, Any],
    number_payload: Mapping[str, Any],
    webhook_payload: Mapping[str, Any] | None,
    allowed_number: str,
    home_channel: str,
    webhook_path: str = DEFAULT_WEBHOOK_PATH,
    webhook_port: int = DEFAULT_WEBHOOK_PORT,
    webhook_host: str = DEFAULT_WEBHOOK_HOST,
    api_key_payload: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Build OP env vars from setup API responses."""
    api_key = _payload_api_key(verify_payload) or _payload_api_key(api_key_payload or {})
    if not api_key:
        raise ValueError("No OP API key found in verification/bootstrap or created-key response")
    number = _payload_phone(number_payload)
    env = {
        "OP_API_KEY": api_key,
        "OP_ALLOWED_NUMBERS": normalize_phone(allowed_number),
        "OP_HOME_CHANNEL": normalize_phone(home_channel),
        "OP_WEBHOOK_HOST": webhook_host,
        "OP_WEBHOOK_PORT": str(webhook_port),
        "OP_WEBHOOK_PATH": webhook_path if webhook_path.startswith("/") else f"/{webhook_path}",
    }
    if number:
        env["OP_PHONE_NUMBER"] = number
    secret = ""
    if webhook_payload:
        secret = str(webhook_payload.get("secret") or webhook_payload.get("webhook_secret") or "")
    if secret:
        env["OP_WEBHOOK_SECRET"] = secret
    return env


def default_env_path() -> Path:
    """Return the Hermes .env path without importing Hermes internals."""
    if os.getenv("HERMES_ENV_PATH"):
        return Path(os.environ["HERMES_ENV_PATH"]).expanduser()
    home = Path(os.getenv("HERMES_HOME") or Path.home() / ".hermes")
    return home / ".env"


def update_env_file(path: str | Path, updates: Mapping[str, str]) -> None:
    """Update or append env vars while preserving unrelated lines."""
    env_path = Path(path).expanduser()
    env_path.parent.mkdir(parents=True, exist_ok=True)
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    pending = {str(k): str(v) for k, v in updates.items() if v is not None}
    output: list[str] = []
    for line in lines:
        key, sep, _ = line.partition("=")
        if sep and key in pending:
            output.append(f"{key}={pending.pop(key)}")
        else:
            output.append(line)
    for key, value in pending.items():
        output.append(f"{key}={value}")
    env_path.write_text("\n".join(output).rstrip("\n") + "\n", encoding="utf-8")


async def run_setup_async(
    *,
    phone: str,
    code: str | None = None,
    webhook_url: str | None = None,
    allowed_number: str | None = None,
    home_channel: str | None = None,
    env_path: str | Path | None = None,
    webhook_path: str = DEFAULT_WEBHOOK_PATH,
    webhook_port: int = DEFAULT_WEBHOOK_PORT,
    webhook_host: str = DEFAULT_WEBHOOK_HOST,
    api_base: str | None = None,
    print_fn: Callable[[str], None] = print,
    code_prompt_fn: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Run non-interactive dashboardless setup after the user has an OTP."""
    import aiohttp

    phone = normalize_phone(phone)
    allowed_number = normalize_phone(allowed_number or phone)
    home_channel = normalize_phone(home_channel or phone)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        client = OPClient("", session, api_base=api_base or "https://api.op.inc")
        status, start_payload = await client.auth_start(phone)
        if status >= 400:
            raise RuntimeError(format_op_error(status, start_payload))
        verification_id = str(start_payload.get("verification_id") if isinstance(start_payload, Mapping) else "")
        if not verification_id:
            raise RuntimeError("OP auth/start did not return verification_id")
        print_fn(f"Sent OP verification code to {phone}.")
        if not code:
            prompt = code_prompt_fn or getpass.getpass
            code = prompt("OP OTP code: ").strip()
        status, verify_payload = await client.auth_verify(verification_id, code)
        if status >= 400:
            raise RuntimeError(format_op_error(status, verify_payload))
        if not isinstance(verify_payload, Mapping):
            raise RuntimeError("OP auth/verify returned an unexpected payload")
        session_token = str(verify_payload.get("session_token") or "")
        setup_client = OPClient(_payload_api_key(verify_payload), session, api_base=api_base or "https://api.op.inc")
        status, numbers_payload = await setup_client.list_my_numbers(session_token=session_token)
        if status >= 400:
            raise RuntimeError(format_op_error(status, numbers_payload))
        number_payload = extract_number_payload(numbers_payload)
        number_id = _payload_id(number_payload)
        key_payload: Mapping[str, Any] = {}
        if not _payload_api_key(verify_payload):
            if not number_id:
                raise RuntimeError("Could not identify an OP number to create an API key for")
            status, created_key = await setup_client.create_api_key(number_id, name="Hermes", session_token=session_token)
            if status >= 400:
                raise RuntimeError(format_op_error(status, created_key))
            key_payload = created_key if isinstance(created_key, Mapping) else {}
        webhook_payload: Mapping[str, Any] | None = None
        if webhook_url:
            if not number_id:
                raise RuntimeError("Could not identify an OP number to create a webhook for")
            status, created_webhook = await setup_client.create_webhook(number_id, webhook_url, session_token=session_token)
            if status >= 400:
                raise RuntimeError(format_op_error(status, created_webhook))
            webhook_payload = created_webhook if isinstance(created_webhook, Mapping) else {}
        updates = build_env_updates(
            verify_payload=verify_payload,
            number_payload=number_payload,
            api_key_payload=key_payload,
            webhook_payload=webhook_payload,
            allowed_number=allowed_number,
            home_channel=home_channel,
            webhook_path=webhook_path,
            webhook_port=webhook_port,
            webhook_host=webhook_host,
        )
        target = Path(env_path) if env_path else default_env_path()
        update_env_file(target, updates)
        print_fn(f"Saved OP config to {target}")
        print_fn(f"OP_API_KEY={redact_secret(updates.get('OP_API_KEY', ''))}")
        if updates.get("OP_WEBHOOK_SECRET"):
            print_fn(f"OP_WEBHOOK_SECRET={redact_secret(updates['OP_WEBHOOK_SECRET'])}")
        return {"success": True, "env_path": str(target), "updates": updates, "number": dict(number_payload)}


def setup_parser(parser: argparse.ArgumentParser) -> None:
    sub = parser.add_subparsers(dest="op_command")
    setup = sub.add_parser("setup", help="Dashboardless OP setup via OTP")
    setup.add_argument("--phone", help="Personal phone number in E.164 format")
    setup.add_argument("--code", help="OTP code texted by OP")
    setup.add_argument("--webhook-url", help="Public OP webhook URL, e.g. https://x/webhooks/op")
    setup.add_argument("--allowed-number", help="Phone number allowed to chat with Hermes; defaults to --phone")
    setup.add_argument("--home-channel", help="Default SMS target for cron/home delivery; defaults to --phone")
    setup.add_argument("--env-path", help="Hermes .env path; defaults to HERMES_HOME/.env")
    setup.add_argument("--webhook-host", default=DEFAULT_WEBHOOK_HOST)
    setup.add_argument("--webhook-port", type=int, default=DEFAULT_WEBHOOK_PORT)
    setup.add_argument("--webhook-path", default=DEFAULT_WEBHOOK_PATH)
    setup.add_argument("--api-base", default="https://api.op.inc")
    setup.set_defaults(func=handle_cli)

    status = sub.add_parser("status", help="Show OP env/config status")
    status.add_argument("--env-path", help="Hermes .env path; defaults to HERMES_HOME/.env")
    status.set_defaults(func=handle_cli)


def _prompt_missing(args: argparse.Namespace) -> None:
    if not getattr(args, "phone", None):
        args.phone = input("Personal phone number (+14155551234): ").strip()


def handle_cli(args: argparse.Namespace) -> int:
    command = getattr(args, "op_command", None)
    if command == "setup":
        _prompt_missing(args)
        asyncio.run(
            run_setup_async(
                phone=args.phone,
                code=args.code,
                webhook_url=args.webhook_url,
                allowed_number=args.allowed_number,
                home_channel=args.home_channel,
                env_path=args.env_path,
                webhook_path=args.webhook_path,
                webhook_port=args.webhook_port,
                webhook_host=args.webhook_host,
                api_base=args.api_base,
            )
        )
        print("Restart Hermes/gateway for OP env changes to take effect.")
        return 0
    if command == "status":
        env_path = Path(args.env_path).expanduser() if args.env_path else default_env_path()
        print(f"OP env path: {env_path}")
        if not env_path.exists():
            print("No .env file found.")
            return 1
        values = {}
        for line in env_path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.startswith("OP_"):
                values[key] = value
        for key in sorted(values):
            value = redact_secret(values[key]) if "KEY" in key or "SECRET" in key else values[key]
            print(f"{key}={value}")
        return 0
    print("Run `hermes op setup` or `hermes op status`.")
    return 2


def register_cli(ctx) -> None:
    if hasattr(ctx, "register_cli_command"):
        ctx.register_cli_command(
            name="op",
            help="Configure and inspect OP SMS integration",
            description="Dashboardless OP SMS setup for Hermes Agent.",
            setup_fn=setup_parser,
            handler_fn=handle_cli,
        )
