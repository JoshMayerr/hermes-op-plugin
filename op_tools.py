"""Hermes tool registrations for the OP API."""

from __future__ import annotations

import json
import os
from typing import Any, Mapping

try:
    from .op_client import OPClient, format_op_error
except ImportError:  # pragma: no cover - top-level pytest/import fallback
    from op_client import OPClient, format_op_error  # type: ignore


def check_requirements() -> bool:
    return bool(os.getenv("OP_API_KEY", "").strip())


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)


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
    to = str(args.get("to") or "").strip()
    body = str(args.get("body") or "")
    idempotency_key = args.get("idempotency_key")
    if not to or not body:
        return _json({"success": False, "error": "to and body are required"})
    result = await _with_client(lambda client: client.send_message(to, body, idempotency_key=str(idempotency_key) if idempotency_key else None))
    return _json(result)


async def op_list_messages(args: Mapping[str, Any] | None = None, **kwargs) -> str:
    args = args or {}
    direction = args.get("direction")
    cursor = args.get("cursor")
    limit = int(args.get("limit") or 50)
    result = await _with_client(
        lambda client: client.list_messages(
            direction=str(direction) if direction else None,
            cursor=str(cursor) if cursor else None,
            limit=limit,
        )
    )
    return _json(result)


async def op_get_message(args: Mapping[str, Any] | None = None, **kwargs) -> str:
    args = args or {}
    message_id = str(args.get("message_id") or "").strip()
    if not message_id:
        return _json({"success": False, "error": "message_id is required"})
    result = await _with_client(lambda client: client.get_message(message_id))
    return _json(result)


async def op_get_number(args: Mapping[str, Any] | None = None, **kwargs) -> str:
    result = await _with_client(lambda client: client.get_number())
    return _json(result)


SEND_SMS_SCHEMA = {
    "name": "op_send_sms",
    "description": "Send an SMS using the configured OP phone number. Use E.164 phone numbers.",
    "parameters": {
        "type": "object",
        "properties": {
            "to": {"type": "string", "description": "Recipient phone number in E.164 format, e.g. +14155551234"},
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
    "description": "Get the OP number associated with the configured API key.",
    "parameters": {"type": "object", "properties": {}},
}


def register_tools(ctx) -> None:
    if not hasattr(ctx, "register_tool"):
        return
    for name, schema, handler in [
        ("op_send_sms", SEND_SMS_SCHEMA, op_send_sms),
        ("op_list_messages", LIST_MESSAGES_SCHEMA, op_list_messages),
        ("op_get_message", GET_MESSAGE_SCHEMA, op_get_message),
        ("op_get_number", GET_NUMBER_SCHEMA, op_get_number),
    ]:
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
