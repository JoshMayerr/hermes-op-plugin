"""Small async client and pure helpers for the OP SMS API."""

from __future__ import annotations

import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Any, Mapping

OP_API_BASE = "https://api.op.inc"
OP_API_VERSION = "/v1"
MAX_OP_SMS_LENGTH = 1600
SIGNATURE_TOLERANCE_SECONDS = 300


@dataclass(frozen=True)
class ParsedSignature:
    timestamp: int
    v1: str


def parse_signature_header(header: str) -> ParsedSignature | None:
    """Parse ``op-signature: t=<unix>,v1=<hex>``."""
    parts: dict[str, str] = {}
    for item in (header or "").split(","):
        key, sep, value = item.strip().partition("=")
        if sep and key:
            parts[key] = value
    try:
        timestamp = int(parts.get("t", ""))
    except ValueError:
        return None
    v1 = parts.get("v1", "").strip().lower()
    if not timestamp or not v1:
        return None
    return ParsedSignature(timestamp=timestamp, v1=v1)


def compute_webhook_signature(secret: str, timestamp: int | str, raw_body: bytes) -> str:
    """Return OP's HMAC-SHA256 webhook signature hex digest."""
    signed = f"{timestamp}.".encode("utf-8") + raw_body
    return hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()


def verify_webhook_signature(
    secret: str,
    signature_header: str,
    raw_body: bytes,
    *,
    now: float | None = None,
    tolerance_seconds: int = SIGNATURE_TOLERANCE_SECONDS,
) -> bool:
    """Validate OP's webhook signature and reject stale timestamps."""
    parsed = parse_signature_header(signature_header)
    if not secret or parsed is None:
        return False
    current = time.time() if now is None else now
    if abs(current - parsed.timestamp) > tolerance_seconds:
        return False
    expected = compute_webhook_signature(secret, parsed.timestamp, raw_body)
    return hmac.compare_digest(expected, parsed.v1)


def format_op_error(status: int, body: Any) -> str:
    """Format OP error responses into concise, readable strings."""
    if isinstance(body, Mapping):
        err = body.get("error")
        if isinstance(err, Mapping):
            code = str(err.get("code") or "error")
            message = str(err.get("message") or "")
            field = err.get("field")
            suffix = f" ({field})" if field else ""
            return f"OP {status} {code}: {message}{suffix}".strip()
        if "message" in body:
            return f"OP {status}: {body['message']}"
    return f"OP {status}: {body}"


class OPClient:
    """Minimal aiohttp-backed OP /v1 client."""

    def __init__(self, api_key: str, session, *, api_base: str = OP_API_BASE) -> None:
        self.api_key = api_key
        self.session = session
        self.api_base = api_base.rstrip("/")

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def send_message(self, to: str, body: str) -> tuple[int, Any]:
        url = f"{self.api_base}{OP_API_VERSION}/messages"
        async with self.session.post(
            url,
            json={"to": to, "body": body},
            headers=self.headers,
        ) as resp:
            try:
                payload = await resp.json()
            except Exception:
                payload = await resp.text()
            return resp.status, payload

    async def get_message(self, message_id: str) -> tuple[int, Any]:
        url = f"{self.api_base}{OP_API_VERSION}/messages/{message_id}"
        async with self.session.get(url, headers=self.headers) as resp:
            try:
                payload = await resp.json()
            except Exception:
                payload = await resp.text()
            return resp.status, payload
