"""Hermes platform adapter plugin for OP SMS."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections import OrderedDict
from typing import Any, Mapping, Optional

from gateway.config import Platform, PlatformConfig
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
    is_network_accessible,
)
from gateway.platforms.helpers import redact_phone, strip_markdown

try:  # Normal Hermes plugin package import path.
    from .op_client import (
        MAX_OP_SMS_LENGTH,
        OPClient,
        format_op_error,
        verify_webhook_signature,
    )
except ImportError:  # Pytest may import repo-root __init__.py as a top-level module.
    from op_client import (  # type: ignore
        MAX_OP_SMS_LENGTH,
        OPClient,
        format_op_error,
        verify_webhook_signature,
    )

logger = logging.getLogger(__name__)

DEFAULT_WEBHOOK_HOST = "127.0.0.1"
DEFAULT_WEBHOOK_PORT = 8645
DEFAULT_WEBHOOK_PATH = "/webhooks/op"
DEDUP_TTL_SECONDS = 10 * 60
DEDUP_MAX_ENTRIES = 2048


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "y", "on"}


def _split_csv(value: str | None) -> set[str]:
    return {part.strip() for part in str(value or "").split(",") if part.strip()}


def _parse_webhook_port(value: str | None, *, default: int | None = DEFAULT_WEBHOOK_PORT) -> int | None:
    """Parse an OP webhook port without raising during plugin discovery."""
    raw = str(value).strip() if value is not None else ""
    if not raw:
        return default
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return None
    if 1 <= port <= 65535:
        return port
    return None


def _normalize_webhook_path(value: str | None) -> str:
    path = str(value or DEFAULT_WEBHOOK_PATH).strip() or DEFAULT_WEBHOOK_PATH
    if not path.startswith("/"):
        path = f"/{path}"
    return path


def check_requirements() -> bool:
    """Return True when runtime deps and required env are present."""
    try:
        import aiohttp  # noqa: F401
    except ImportError:
        return False
    return bool(os.getenv("OP_API_KEY"))


def validate_config(config: PlatformConfig | None = None) -> bool:
    """Validate env-driven OP configuration."""
    if not os.getenv("OP_API_KEY"):
        logger.error("[op] OP_API_KEY is required")
        return False
    if _parse_webhook_port(os.getenv("OP_WEBHOOK_PORT")) is None:
        logger.error("[op] OP_WEBHOOK_PORT must be an integer from 1 to 65535")
        return False
    path = _normalize_webhook_path(os.getenv("OP_WEBHOOK_PATH", DEFAULT_WEBHOOK_PATH))
    if not path or any(ch.isspace() for ch in path):
        logger.error("[op] OP_WEBHOOK_PATH must be a non-empty path without whitespace")
        return False
    host = os.getenv("OP_WEBHOOK_HOST", DEFAULT_WEBHOOK_HOST).strip() or DEFAULT_WEBHOOK_HOST
    webhook_secret = os.getenv("OP_WEBHOOK_SECRET", "").strip()
    insecure_no_signature = _truthy(os.getenv("OP_INSECURE_NO_SIGNATURE"))
    if not webhook_secret and not (insecure_no_signature and not is_network_accessible(host)):
        logger.error("[op] OP_WEBHOOK_SECRET is required unless insecure loopback-only development mode is enabled")
        return False
    return True


def _env_enablement() -> dict[str, Any] | None:
    if not os.getenv("OP_API_KEY"):
        return None
    return {
        "webhook_host": os.getenv("OP_WEBHOOK_HOST", DEFAULT_WEBHOOK_HOST),
        "webhook_port": _parse_webhook_port(os.getenv("OP_WEBHOOK_PORT"), default=DEFAULT_WEBHOOK_PORT),
        "webhook_path": _normalize_webhook_path(os.getenv("OP_WEBHOOK_PATH", DEFAULT_WEBHOOK_PATH)),
        "home_channel": os.getenv("OP_HOME_CHANNEL", ""),
    }


async def _standalone_send(
    pconfig,
    chat_id: str,
    message: str,
    *,
    thread_id=None,
    media_files=None,
    force_document: bool = False,
) -> dict:
    """Send through OP without a live gateway adapter (cron/notifications)."""
    import aiohttp

    api_key = os.getenv("OP_API_KEY", "").strip()
    if not api_key:
        return {"error": "OP_API_KEY is not set"}
    if media_files:
        return {"error": "OP SMS does not support media attachments"}

    text = strip_markdown(message or "")
    chunks = BasePlatformAdapter.truncate_message(text, max_length=MAX_OP_SMS_LENGTH)
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        client = OPClient(api_key, session)
        last_id = ""
        continuation: list[str] = []
        for chunk in chunks:
            status, payload = await client.send_message(chat_id, chunk)
            if status >= 400:
                return {"error": format_op_error(status, payload)}
            msg_id = str(payload.get("id", "")) if isinstance(payload, Mapping) else ""
            if last_id:
                continuation.append(last_id)
            last_id = msg_id
        return {"success": True, "message_id": last_id, "continuation_message_ids": continuation}


class OPAdapter(BasePlatformAdapter):
    """OP SMS platform adapter."""

    MAX_MESSAGE_LENGTH = MAX_OP_SMS_LENGTH

    def __init__(self, config: PlatformConfig):
        # Plugin platforms are not members of Hermes' built-in Platform enum.
        # BasePlatformAdapter and gateway routing normalize either enum values
        # or raw strings, so keep standalone/plugin platforms as their string key.
        super().__init__(config, "op")  # type: ignore[arg-type]
        self._api_key = os.getenv("OP_API_KEY", "").strip()
        self._webhook_secret = os.getenv("OP_WEBHOOK_SECRET", "").strip()
        self._webhook_host = os.getenv("OP_WEBHOOK_HOST", DEFAULT_WEBHOOK_HOST).strip() or DEFAULT_WEBHOOK_HOST
        self._webhook_port = _parse_webhook_port(os.getenv("OP_WEBHOOK_PORT"), default=DEFAULT_WEBHOOK_PORT)
        self._webhook_path = _normalize_webhook_path(os.getenv("OP_WEBHOOK_PATH", DEFAULT_WEBHOOK_PATH))
        self._allow_all_numbers = _truthy(os.getenv("OP_ALLOW_ALL_NUMBERS"))
        self._allowed_numbers = _split_csv(os.getenv("OP_ALLOWED_NUMBERS"))
        self._insecure_no_signature = _truthy(os.getenv("OP_INSECURE_NO_SIGNATURE"))
        self._own_number = os.getenv("OP_PHONE_NUMBER", "").strip()
        self._runner = None
        self._http_session: Optional["aiohttp.ClientSession"] = None
        self._client: OPClient | None = None
        self._seen_deliveries: OrderedDict[str, float] = OrderedDict()

    async def connect(self) -> bool:
        import aiohttp
        from aiohttp import web

        if not self._api_key:
            msg = "[op] OP_API_KEY is required"
            logger.error(msg)
            self._set_fatal_error("op_missing_api_key", msg, retryable=False)
            return False
        if self._api_key and not self._api_key.startswith("op_live_"):
            logger.warning("[op] OP_API_KEY does not start with op_live_; continuing for test/dev compatibility")

        if self._webhook_port is None:
            msg = "[op] OP_WEBHOOK_PORT must be an integer from 1 to 65535"
            logger.error(msg)
            self._set_fatal_error("op_invalid_webhook_port", msg, retryable=False)
            return False
        if not self._webhook_path or any(ch.isspace() for ch in self._webhook_path):
            msg = "[op] OP_WEBHOOK_PATH must be a non-empty path without whitespace"
            logger.error(msg)
            self._set_fatal_error("op_invalid_webhook_path", msg, retryable=False)
            return False

        if not self._webhook_secret:
            if not (self._insecure_no_signature and not is_network_accessible(self._webhook_host)):
                msg = (
                    "[op] Refusing to start webhook without OP_WEBHOOK_SECRET. "
                    "For loopback-only local development you may set "
                    "OP_INSECURE_NO_SIGNATURE=true, but do not use that in production."
                )
                logger.error(msg)
                self._set_fatal_error("op_missing_webhook_secret", msg, retryable=False)
                return False
            logger.warning(
                "[op] OP_INSECURE_NO_SIGNATURE=true on loopback host %s — webhook signatures are DISABLED",
                self._webhook_host,
            )

        if not self._allow_all_numbers and not self._allowed_numbers:
            logger.warning("[op] no OP_ALLOWED_NUMBERS configured; inbound SMS will be rejected until allowlist is set")

        app = web.Application()
        app.router.add_post(self._webhook_path, self._handle_webhook)
        app.router.add_get("/health", lambda _: web.json_response({"ok": True, "platform": "op"}))

        try:
            self._runner = web.AppRunner(app)
            await self._runner.setup()
            site = web.TCPSite(self._runner, self._webhook_host, self._webhook_port)
            await site.start()

            self._http_session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
            self._client = OPClient(self._api_key, self._http_session)
            self._running = True
            logger.info("[op] webhook server listening on %s:%d%s", self._webhook_host, self._webhook_port, self._webhook_path)
            return True
        except Exception as exc:
            msg = f"[op] failed to start webhook server: {exc}"
            logger.error(msg)
            await self.disconnect()
            self._set_fatal_error("op_webhook_start_failed", msg, retryable=True)
            return False

    async def disconnect(self) -> None:
        if self._http_session:
            await self._http_session.close()
            self._http_session = None
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
        self._client = None
        self._running = False
        logger.info("[op] Disconnected")

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> SendResult:
        import aiohttp

        if not self._api_key:
            return SendResult(success=False, error="OP_API_KEY is not set")
        formatted = self.format_message(content)
        chunks = self.truncate_message(formatted, max_length=MAX_OP_SMS_LENGTH)

        owns_session = self._http_session is not None
        session = self._http_session or aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=30))
        client = self._client or OPClient(self._api_key, session)
        last_id = ""
        continuation: list[str] = []
        try:
            for chunk in chunks:
                try:
                    status, payload = await client.send_message(chat_id, chunk)
                except Exception as exc:
                    logger.error("[op] send error to %s: %s", redact_phone(chat_id), exc)
                    return SendResult(success=False, error=str(exc), retryable=True)
                if status >= 400:
                    error = format_op_error(status, payload)
                    logger.error("[op] send failed to %s: %s", redact_phone(chat_id), error)
                    return SendResult(success=False, error=error, raw_response=payload, retryable=status in {429, 500, 502, 503, 504})
                msg_id = str(payload.get("id", "")) if isinstance(payload, Mapping) else ""
                if last_id:
                    continuation.append(last_id)
                last_id = msg_id
        finally:
            if not owns_session and session:
                await session.close()
        return SendResult(success=True, message_id=last_id, continuation_message_ids=tuple(continuation))

    async def get_chat_info(self, chat_id: str) -> dict[str, Any]:
        return {"name": chat_id, "type": "dm"}

    def format_message(self, content: str) -> str:
        return strip_markdown(content or "")

    def _prune_dedupe(self, now: float | None = None) -> None:
        current = time.time() if now is None else now
        expired = [
            delivery_id
            for delivery_id, ts in self._seen_deliveries.items()
            if current - ts > DEDUP_TTL_SECONDS
        ]
        for delivery_id in expired:
            self._seen_deliveries.pop(delivery_id, None)
        while len(self._seen_deliveries) > DEDUP_MAX_ENTRIES:
            self._seen_deliveries.popitem(last=False)

    def _is_duplicate_delivery(self, delivery_id: str | None, message_id: str | None = None, now: float | None = None) -> bool:
        keys = []
        if delivery_id:
            keys.append(f"delivery:{delivery_id}")
        if message_id:
            keys.append(f"message:{message_id}")
        if not keys:
            return False
        current = time.time() if now is None else now
        self._prune_dedupe(current)
        duplicate = any(key in self._seen_deliveries for key in keys)
        for key in keys:
            self._seen_deliveries[key] = current
            self._seen_deliveries.move_to_end(key)
        return duplicate

    def _authorized_sender(self, phone: str) -> bool:
        return self._allow_all_numbers or phone in self._allowed_numbers

    @staticmethod
    def _unwrap_payload(payload: Any) -> tuple[str, Mapping[str, Any]]:
        if not isinstance(payload, Mapping):
            return "", {}
        event_type = str(payload.get("type") or payload.get("event") or "")
        data = payload.get("message")
        if not isinstance(data, Mapping):
            data = payload.get("data")
        if isinstance(data, Mapping) and isinstance(data.get("message"), Mapping):
            data = data["message"]
        if not isinstance(data, Mapping):
            data = payload
        return event_type, data

    @staticmethod
    def extract_inbound_message(payload: Any) -> dict[str, Any] | None:
        """Extract a robust inbound message view from plausible OP webhook payloads."""
        event_type, msg = OPAdapter._unwrap_payload(payload)
        if not msg:
            return None
        direction = str(msg.get("direction") or "").lower()
        status = str(msg.get("status") or "").lower()
        event_lower = event_type.lower()
        is_inbound = (
            direction == "inbound"
            or "message.inbound" in event_lower
            or ("inbound" in event_lower and "status" not in event_lower)
        )
        sender = str(msg.get("from") or msg.get("from_number") or msg.get("sender") or "").strip()
        recipient = str(msg.get("to") or msg.get("to_number") or msg.get("recipient") or "").strip()
        body = str(msg.get("body") or msg.get("text") or msg.get("message") or "").strip()
        if not is_inbound:
            # OP may deliver the message object directly; accept only when it does
            # not look like an outbound terminal status event.
            is_status_event = status in {"queued", "sending", "sent", "failed", "delivered"} or "status" in event_lower
            is_inbound = bool(sender and body and direction != "outbound" and not is_status_event)
        if not (is_inbound and sender and body):
            return None
        return {
            "id": str(msg.get("id") or msg.get("message_id") or ""),
            "from": sender,
            "to": recipient,
            "body": body,
            "direction": direction or "inbound",
            "message": dict(msg),
            "event_type": event_type,
        }

    async def _handle_webhook(self, request):
        from aiohttp import web

        raw = await request.read()
        delivery_id = request.headers.get("op-delivery-id", "").strip()

        if self._webhook_secret:
            sig = request.headers.get("op-signature", "")
            if not verify_webhook_signature(self._webhook_secret, sig, raw):
                logger.warning("[op] rejected webhook: invalid or stale signature")
                return web.json_response({"success": False, "error": "invalid signature"}, status=403)
        elif not self._insecure_no_signature:
            return web.json_response({"success": False, "error": "signature required"}, status=403)

        try:
            payload = json.loads(raw.decode("utf-8"))
        except Exception as exc:
            logger.warning("[op] webhook JSON parse error: %s", exc)
            return web.json_response({"success": False, "error": "invalid json"}, status=400)

        inbound = self.extract_inbound_message(payload)
        if inbound is None:
            logger.debug("[op] ignoring non-inbound webhook payload")
            return web.json_response({"success": True, "ignored": True})

        message_id = inbound["id"]
        if self._is_duplicate_delivery(delivery_id, message_id):
            logger.info("[op] duplicate webhook ignored: delivery_id=%s message_id=%s", delivery_id or "-", message_id or "-")
            return web.json_response({"success": True, "duplicate": True})

        from_number = inbound["from"]
        if self._own_number and from_number == self._own_number:
            logger.debug("[op] ignoring echo from own number %s", redact_phone(from_number))
            return web.json_response({"success": True, "ignored": True})
        if not self._authorized_sender(from_number):
            logger.warning("[op] rejected unauthorized sender %s", redact_phone(from_number))
            return web.json_response({"success": True, "ignored": True, "reason": "unauthorized"})

        logger.info(
            "[op] inbound from %s: delivery_id=%s message_id=%s",
            redact_phone(from_number),
            delivery_id or "-",
            message_id or "-",
        )
        source = self.build_source(
            chat_id=from_number,
            chat_name=from_number,
            chat_type="dm",
            user_id=from_number,
            user_name=from_number,
            message_id=message_id or delivery_id or None,
        )
        event = MessageEvent(
            text=inbound["body"],
            message_type=MessageType.TEXT,
            source=source,
            raw_message={"payload": payload, "delivery_id": delivery_id},
            message_id=message_id or delivery_id,
        )
        task = asyncio.create_task(self.handle_message(event))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)
        return web.json_response({"success": True})


def register(ctx) -> None:
    """Plugin entry point — called by Hermes plugin system."""
    ctx.register_platform(
        name="op",
        label="OP",
        adapter_factory=lambda cfg: OPAdapter(cfg),
        check_fn=check_requirements,
        validate_config=validate_config,
        required_env=["OP_API_KEY"],
        env_enablement_fn=_env_enablement,
        cron_deliver_env_var="OP_HOME_CHANNEL",
        allowed_users_env="OP_ALLOWED_NUMBERS",
        allow_all_env="OP_ALLOW_ALL_NUMBERS",
        max_message_length=MAX_OP_SMS_LENGTH,
        pii_safe=True,
        platform_hint="You are chatting via SMS through OP. Keep replies concise. Avoid markdown-heavy formatting.",
        emoji="📱",
        standalone_sender_fn=_standalone_send,
    )
