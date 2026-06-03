"""Hermes tool registrations for the OP API."""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Mapping

try:
    from .op_client import DEFAULT_WEBHOOK_EVENTS, OPClient, format_op_error
except ImportError:  # pragma: no cover - top-level pytest/import fallback
    from op_client import DEFAULT_WEBHOOK_EVENTS, OPClient, format_op_error  # type: ignore


def check_requirements() -> bool:
    return bool(os.getenv("OP_API_KEY", "").strip())


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)


def _str_arg(args: Mapping[str, Any], key: str) -> str:
    value = args.get(key)
    return str(value).strip() if value is not None else ""


def _optional_str(args: Mapping[str, Any], key: str) -> str | None:
    value = _str_arg(args, key)
    return value or None


def _optional_bool(args: Mapping[str, Any], key: str) -> bool | None:
    if key not in args or args.get(key) is None:
        return None
    value = args.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y", "on"}:
            return True
        if normalized in {"false", "0", "no", "n", "off"}:
            return False
    return bool(value)


def _string_list_arg(args: Mapping[str, Any], key: str) -> list[str] | None:
    value = args.get(key)
    if value is None or value == "":
        return None
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()]


def _confirmed(args: Mapping[str, Any]) -> bool:
    return _optional_bool(args, "confirm") is True


async def _with_client(operation):
    import aiohttp

    api_key = os.getenv("OP_API_KEY", "").strip()
    if not api_key:
        return {"success": False, "error": "OP_API_KEY is not set"}
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30)) as session:
        client = OPClient(api_key, session, api_base=os.getenv("OP_API_BASE", "https://api.op.inc"))
        status, payload = await operation(client)
        if status >= 400:
            return {"success": False, "error": format_op_error(status, payload), "status": status, "payload": payload}
        return {"success": True, "status": status, "data": payload}


async def op_send_sms(args: Mapping[str, Any] | None = None, **kwargs) -> str:
    args = args or {}
    to = _str_arg(args, "to")
    body = str(args.get("body") or "")
    idempotency_key = _optional_str(args, "idempotency_key")
    if not to or not body:
        return _json({"success": False, "error": "to and body are required"})
    result = await _with_client(lambda client: client.send_message(to, body, idempotency_key=idempotency_key))
    return _json(result)


async def op_list_messages(args: Mapping[str, Any] | None = None, **kwargs) -> str:
    args = args or {}
    direction = _optional_str(args, "direction")
    cursor = _optional_str(args, "cursor")
    limit = int(args.get("limit") or 50)
    result = await _with_client(
        lambda client: client.list_messages(
            direction=direction,
            cursor=cursor,
            limit=limit,
        )
    )
    return _json(result)


async def op_get_message(args: Mapping[str, Any] | None = None, **kwargs) -> str:
    args = args or {}
    message_id = _str_arg(args, "message_id")
    if not message_id:
        return _json({"success": False, "error": "message_id is required"})
    result = await _with_client(lambda client: client.get_message(message_id))
    return _json(result)


async def op_get_number(args: Mapping[str, Any] | None = None, **kwargs) -> str:
    result = await _with_client(lambda client: client.get_number())
    return _json(result)


async def op_list_my_numbers(args: Mapping[str, Any] | None = None, **kwargs) -> str:
    result = await _with_client(lambda client: client.list_my_numbers())
    return _json(result)


async def op_list_available_numbers(args: Mapping[str, Any] | None = None, **kwargs) -> str:
    result = await _with_client(lambda client: client.list_available_numbers())
    return _json(result)


async def op_lease_number(args: Mapping[str, Any] | None = None, **kwargs) -> str:
    args = args or {}
    number_id = _str_arg(args, "number_id")
    if not number_id:
        return _json({"success": False, "error": "number_id is required"})
    result = await _with_client(lambda client: client.lease_number(number_id))
    return _json(result)


async def op_release_number(args: Mapping[str, Any] | None = None, **kwargs) -> str:
    args = args or {}
    number_id = _str_arg(args, "number_id")
    if not number_id:
        return _json({"success": False, "error": "number_id is required"})
    if not _confirmed(args):
        return _json({"success": False, "error": "Set confirm=true to release an OP number. This is destructive."})
    result = await _with_client(lambda client: client.release_number(number_id))
    return _json(result)


async def op_list_api_keys(args: Mapping[str, Any] | None = None, **kwargs) -> str:
    args = args or {}
    number_id = _optional_str(args, "number_id")
    result = await _with_client(lambda client: client.list_api_keys(number_id=number_id))
    return _json(result)


async def op_create_api_key(args: Mapping[str, Any] | None = None, **kwargs) -> str:
    args = args or {}
    number_id = _str_arg(args, "number_id")
    name = _optional_str(args, "name") or "Hermes"
    if not number_id:
        return _json({"success": False, "error": "number_id is required"})
    result = await _with_client(lambda client: client.create_api_key(number_id, name=name))
    return _json(result)


async def op_revoke_api_key(args: Mapping[str, Any] | None = None, **kwargs) -> str:
    args = args or {}
    key_id = _str_arg(args, "key_id")
    if not key_id:
        return _json({"success": False, "error": "key_id is required"})
    if not _confirmed(args):
        return _json({"success": False, "error": "Set confirm=true to revoke an OP API key. This is destructive."})
    result = await _with_client(lambda client: client.revoke_api_key(key_id))
    return _json(result)


async def op_list_webhooks(args: Mapping[str, Any] | None = None, **kwargs) -> str:
    args = args or {}
    number_id = _optional_str(args, "number_id")
    result = await _with_client(lambda client: client.list_webhooks(number_id=number_id))
    return _json(result)


async def op_create_webhook(args: Mapping[str, Any] | None = None, **kwargs) -> str:
    args = args or {}
    number_id = _str_arg(args, "number_id")
    url = _str_arg(args, "url")
    events = _string_list_arg(args, "events")
    if not number_id or not url:
        return _json({"success": False, "error": "number_id and url are required"})
    result = await _with_client(lambda client: client.create_webhook(number_id, url, events=events))
    return _json(result)


async def op_update_webhook(args: Mapping[str, Any] | None = None, **kwargs) -> str:
    args = args or {}
    webhook_id = _str_arg(args, "webhook_id")
    url = _optional_str(args, "url")
    events = _string_list_arg(args, "events")
    disabled = _optional_bool(args, "disabled")
    if not webhook_id:
        return _json({"success": False, "error": "webhook_id is required"})
    if url is None and events is None and disabled is None:
        return _json({"success": False, "error": "At least one of url, events, or disabled is required"})
    result = await _with_client(lambda client: client.update_webhook(webhook_id, url=url, events=events, disabled=disabled))
    return _json(result)


async def op_test_webhook(args: Mapping[str, Any] | None = None, **kwargs) -> str:
    args = args or {}
    webhook_id = _str_arg(args, "webhook_id")
    if not webhook_id:
        return _json({"success": False, "error": "webhook_id is required"})
    result = await _with_client(lambda client: client.test_webhook(webhook_id))
    return _json(result)


async def op_rotate_webhook_secret(args: Mapping[str, Any] | None = None, **kwargs) -> str:
    args = args or {}
    webhook_id = _str_arg(args, "webhook_id")
    if not webhook_id:
        return _json({"success": False, "error": "webhook_id is required"})
    if not _confirmed(args):
        return _json({"success": False, "error": "Set confirm=true to rotate an OP webhook secret. This invalidates the old secret."})
    result = await _with_client(lambda client: client.rotate_webhook_secret(webhook_id))
    return _json(result)


async def op_delete_webhook(args: Mapping[str, Any] | None = None, **kwargs) -> str:
    args = args or {}
    webhook_id = _str_arg(args, "webhook_id")
    if not webhook_id:
        return _json({"success": False, "error": "webhook_id is required"})
    if not _confirmed(args):
        return _json({"success": False, "error": "Set confirm=true to delete an OP webhook. This is destructive."})
    result = await _with_client(lambda client: client.delete_webhook(webhook_id))
    return _json(result)


SEND_SMS_SCHEMA = {
    "name": "op_send_sms",
    "description": "Send an SMS using the configured OP phone number. Use E.164 phone numbers.",
    "parameters": {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient phone number in E.164 format, e.g. +141****1234"},
            "body": {"type": "string", "description": "SMS body, max 1600 characters"},
            "idempotency_key": {"type": "string", "description": "Optional idempotency key for safe retries"},
        },
        "required": ["to", "body"],
    },
}

LIST_MESSAGES_SCHEMA = {
    "name": "op_list_messages",
    "description": "List OP SMS messages for the configured API key.",
    "parameters": {
        "type": "object",
        "properties": {
            "direction": {"type": "string", "enum": ["inbound", "outbound"], "description": "Optional message direction filter"},
            "cursor": {"type": "string", "description": "Pagination cursor"},
            "limit": {"type": "integer", "description": "Maximum messages to return", "default": 50},
        },
    },
}

GET_MESSAGE_SCHEMA = {
    "name": "op_get_message",
    "description": "Get one OP SMS message by id, including current delivery status when available.",
    "parameters": {
        "type": "object",
        "properties": {"message_id": {"type": "string", "description": "OP message id"}},
        "required": ["message_id"],
    },
}

GET_NUMBER_SCHEMA = {
    "name": "op_get_number",
    "description": "Get the OP number associated with the configured runtime API key.",
    "parameters": {"type": "object", "properties": {}},
}

LIST_MY_NUMBERS_SCHEMA = {
    "name": "op_list_my_numbers",
    "description": "List OP numbers owned by the current account using console permissions.",
    "parameters": {"type": "object", "properties": {}},
}

LIST_AVAILABLE_NUMBERS_SCHEMA = {
    "name": "op_list_available_numbers",
    "description": "List OP numbers currently available to lease.",
    "parameters": {"type": "object", "properties": {}},
}

LEASE_NUMBER_SCHEMA = {
    "name": "op_lease_number",
    "description": "Lease an available OP number by number_id for the current account.",
    "parameters": {
        "type": "object",
        "properties": {"number_id": {"type": "string", "description": "OP number id to lease"}},
        "required": ["number_id"],
    },
}

RELEASE_NUMBER_SCHEMA = {
    "name": "op_release_number",
    "description": "Release an OP number. Destructive: only use after explicit user request; requires confirm=true.",
    "parameters": {
        "type": "object",
        "properties": {
            "number_id": {"type": "string", "description": "OP number id to release"},
            "confirm": {"type": "boolean", "description": "Must be true to confirm releasing this number"},
        },
        "required": ["number_id", "confirm"],
    },
}

LIST_API_KEYS_SCHEMA = {
    "name": "op_list_api_keys",
    "description": "List OP API keys for the account, optionally filtered by number_id.",
    "parameters": {
        "type": "object",
        "properties": {"number_id": {"type": "string", "description": "Optional OP number id filter"}},
    },
}

CREATE_API_KEY_SCHEMA = {
    "name": "op_create_api_key",
    "description": "Create an OP API key for a number. The returned secret is sensitive; do not expose it unnecessarily.",
    "parameters": {
        "type": "object",
        "properties": {
            "number_id": {"type": "string", "description": "OP number id the key should access"},
            "name": {"type": "string", "description": "Human-readable key name", "default": "Hermes"},
        },
        "required": ["number_id"],
    },
}

REVOKE_API_KEY_SCHEMA = {
    "name": "op_revoke_api_key",
    "description": "Revoke an OP API key. Destructive: only use after explicit user request; requires confirm=true.",
    "parameters": {
        "type": "object",
        "properties": {
            "key_id": {"type": "string", "description": "OP API key id to revoke"},
            "confirm": {"type": "boolean", "description": "Must be true to confirm revocation"},
        },
        "required": ["key_id", "confirm"],
    },
}

LIST_WEBHOOKS_SCHEMA = {
    "name": "op_list_webhooks",
    "description": "List OP webhooks for the account, optionally filtered by number_id.",
    "parameters": {
        "type": "object",
        "properties": {"number_id": {"type": "string", "description": "Optional OP number id filter"}},
    },
}

CREATE_WEBHOOK_SCHEMA = {
    "name": "op_create_webhook",
    "description": "Create an OP webhook for a number. Use the public HTTPS Hermes OP webhook URL, usually <tunnel>/webhooks/op.",
    "parameters": {
        "type": "object",
        "properties": {
            "number_id": {"type": "string", "description": "OP number id"},
            "url": {"type": "string", "description": "Public HTTPS webhook URL"},
            "events": {
                "type": "array",
                "items": {"type": "string"},
                "description": f"Optional OP event list. Defaults to {', '.join(DEFAULT_WEBHOOK_EVENTS)}",
            },
        },
        "required": ["number_id", "url"],
    },
}

UPDATE_WEBHOOK_SCHEMA = {
    "name": "op_update_webhook",
    "description": "Update an OP webhook URL, events, or disabled flag.",
    "parameters": {
        "type": "object",
        "properties": {
            "webhook_id": {"type": "string", "description": "OP webhook id"},
            "url": {"type": "string", "description": "New public HTTPS webhook URL"},
            "events": {"type": "array", "items": {"type": "string"}, "description": "Replacement OP event list"},
            "disabled": {"type": "boolean", "description": "Set true to disable, false to enable"},
        },
        "required": ["webhook_id"],
    },
}

TEST_WEBHOOK_SCHEMA = {
    "name": "op_test_webhook",
    "description": "Ask OP to send a test delivery to a webhook.",
    "parameters": {
        "type": "object",
        "properties": {"webhook_id": {"type": "string", "description": "OP webhook id"}},
        "required": ["webhook_id"],
    },
}

ROTATE_WEBHOOK_SECRET_SCHEMA = {
    "name": "op_rotate_webhook_secret",
    "description": "Rotate an OP webhook HMAC secret. Credential-changing: update OP_WEBHOOK_SECRET after use; requires confirm=true.",
    "parameters": {
        "type": "object",
        "properties": {
            "webhook_id": {"type": "string", "description": "OP webhook id"},
            "confirm": {"type": "boolean", "description": "Must be true to confirm secret rotation"},
        },
        "required": ["webhook_id", "confirm"],
    },
}

DELETE_WEBHOOK_SCHEMA = {
    "name": "op_delete_webhook",
    "description": "Delete an OP webhook. Destructive: only use after explicit user request; requires confirm=true.",
    "parameters": {
        "type": "object",
        "properties": {
            "webhook_id": {"type": "string", "description": "OP webhook id"},
            "confirm": {"type": "boolean", "description": "Must be true to confirm deletion"},
        },
        "required": ["webhook_id", "confirm"],
    },
}

TOOL_DEFS: list[tuple[str, dict[str, Any], Callable[..., Any]]] = [
    ("op_send_sms", SEND_SMS_SCHEMA, op_send_sms),
    ("op_list_messages", LIST_MESSAGES_SCHEMA, op_list_messages),
    ("op_get_message", GET_MESSAGE_SCHEMA, op_get_message),
    ("op_get_number", GET_NUMBER_SCHEMA, op_get_number),
    ("op_list_my_numbers", LIST_MY_NUMBERS_SCHEMA, op_list_my_numbers),
    ("op_list_available_numbers", LIST_AVAILABLE_NUMBERS_SCHEMA, op_list_available_numbers),
    ("op_lease_number", LEASE_NUMBER_SCHEMA, op_lease_number),
    ("op_release_number", RELEASE_NUMBER_SCHEMA, op_release_number),
    ("op_list_api_keys", LIST_API_KEYS_SCHEMA, op_list_api_keys),
    ("op_create_api_key", CREATE_API_KEY_SCHEMA, op_create_api_key),
    ("op_revoke_api_key", REVOKE_API_KEY_SCHEMA, op_revoke_api_key),
    ("op_list_webhooks", LIST_WEBHOOKS_SCHEMA, op_list_webhooks),
    ("op_create_webhook", CREATE_WEBHOOK_SCHEMA, op_create_webhook),
    ("op_update_webhook", UPDATE_WEBHOOK_SCHEMA, op_update_webhook),
    ("op_test_webhook", TEST_WEBHOOK_SCHEMA, op_test_webhook),
    ("op_rotate_webhook_secret", ROTATE_WEBHOOK_SECRET_SCHEMA, op_rotate_webhook_secret),
    ("op_delete_webhook", DELETE_WEBHOOK_SCHEMA, op_delete_webhook),
]


def register_tools(ctx) -> None:
    if not hasattr(ctx, "register_tool"):
        return
    for name, schema, handler in TOOL_DEFS:
        ctx.register_tool(
            name=name,
            toolset="op",
            schema=schema,
            handler=handler,
            check_fn=check_requirements,
            requires_env=["OP_API_KEY"],
            is_async=True,
            description=schema["description"],
            emoji="📱",
        )
