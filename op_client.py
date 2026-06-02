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
DEFAULT_WEBHOOK_EVENTS = ["message.received", "message.sent", "message.failed"]


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
    """Minimal aiohttp-backed OP API client.

    Runtime messaging uses ``/v1`` bearer keys. Dashboardless setup uses
    ``/auth`` plus ``/console`` endpoints with a session token returned by OTP
    verification.
    """

    def __init__(self, api_key: str, session, *, api_base: str = OP_API_BASE) -> None:
        self.api_key = api_key
        self.session = session
        self.api_base = api_base.rstrip("/")

    @staticmethod
    def _json_headers() -> dict[str, str]:
        return {"Content-Type": "application/json", "Accept": "application/json"}

    @property
    def headers(self) -> dict[str, str]:
        headers = self._json_headers()
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def console_headers(self, session_token: str | None = None) -> dict[str, str]:
        headers = self._json_headers()
        token = (session_token or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        elif self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    async def _request(self, method: str, path: str, *, headers: dict[str, str] | None = None, **kwargs) -> tuple[int, Any]:
        url = f"{self.api_base}{path}"
        call = getattr(self.session, method.lower())
        async with call(url, headers=headers or self.headers, **kwargs) as resp:
            try:
                payload = await resp.json()
            except Exception:
                payload = await resp.text()
            return resp.status, payload

    # /auth — dashboardless signup/login
    async def auth_start(self, phone: str) -> tuple[int, Any]:
        return await self._request(
            "POST",
            "/auth/start",
            json={"phone": phone},
            headers=self._json_headers(),
        )

    async def auth_verify(self, verification_id: str, code: str) -> tuple[int, Any]:
        return await self._request(
            "POST",
            "/auth/verify",
            json={"verification_id": verification_id, "code": code},
            headers=self._json_headers(),
        )

    async def get_verification(self, verification_id: str) -> tuple[int, Any]:
        return await self._request("GET", f"/auth/verifications/{verification_id}", headers=self._json_headers())

    # /console — setup/provisioning with session token or privileged bearer
    async def get_me(self, *, session_token: str | None = None) -> tuple[int, Any]:
        return await self._request("GET", "/console/me", headers=self.console_headers(session_token))

    async def list_my_numbers(self, *, session_token: str | None = None) -> tuple[int, Any]:
        return await self._request("GET", "/console/numbers/mine", headers=self.console_headers(session_token))

    async def list_available_numbers(self) -> tuple[int, Any]:
        return await self._request("GET", "/console/numbers/available", headers=self._json_headers())

    async def lease_number(self, number_id: str, *, session_token: str | None = None) -> tuple[int, Any]:
        return await self._request("POST", f"/console/numbers/{number_id}/lease", headers=self.console_headers(session_token))

    async def release_number(self, number_id: str, *, session_token: str | None = None) -> tuple[int, Any]:
        return await self._request("POST", f"/console/numbers/{number_id}/release", headers=self.console_headers(session_token))

    async def list_api_keys(self, number_id: str | None = None, *, session_token: str | None = None) -> tuple[int, Any]:
        params = {"number_id": number_id} if number_id else None
        return await self._request("GET", "/console/api-keys", headers=self.console_headers(session_token), params=params)

    async def create_api_key(self, number_id: str, *, name: str = "Hermes", session_token: str | None = None) -> tuple[int, Any]:
        return await self._request(
            "POST",
            "/console/api-keys",
            json={"number_id": number_id, "name": name},
            headers=self.console_headers(session_token),
        )

    async def revoke_api_key(self, key_id: str, *, session_token: str | None = None) -> tuple[int, Any]:
        return await self._request("DELETE", f"/console/api-keys/{key_id}", headers=self.console_headers(session_token))

    async def list_webhooks(self, number_id: str | None = None, *, session_token: str | None = None) -> tuple[int, Any]:
        params = {"number_id": number_id} if number_id else None
        return await self._request("GET", "/console/webhooks", headers=self.console_headers(session_token), params=params)

    async def create_webhook(
        self,
        number_id: str,
        url: str,
        *,
        events: list[str] | None = None,
        session_token: str | None = None,
    ) -> tuple[int, Any]:
        return await self._request(
            "POST",
            "/console/webhooks",
            json={"number_id": number_id, "url": url, "events": events or list(DEFAULT_WEBHOOK_EVENTS)},
            headers=self.console_headers(session_token),
        )

    async def update_webhook(
        self,
        webhook_id: str,
        *,
        url: str | None = None,
        events: list[str] | None = None,
        disabled: bool | None = None,
        session_token: str | None = None,
    ) -> tuple[int, Any]:
        body: dict[str, Any] = {}
        if url is not None:
            body["url"] = url
        if events is not None:
            body["events"] = events
        if disabled is not None:
            body["disabled"] = disabled
        return await self._request(
            "PATCH",
            f"/console/webhooks/{webhook_id}",
            json=body,
            headers=self.console_headers(session_token),
        )

    async def delete_webhook(self, webhook_id: str, *, session_token: str | None = None) -> tuple[int, Any]:
        return await self._request("DELETE", f"/console/webhooks/{webhook_id}", headers=self.console_headers(session_token))

    async def rotate_webhook_secret(self, webhook_id: str, *, session_token: str | None = None) -> tuple[int, Any]:
        return await self._request("POST", f"/console/webhooks/{webhook_id}/rotate-secret", headers=self.console_headers(session_token))

    async def test_webhook(self, webhook_id: str, *, session_token: str | None = None) -> tuple[int, Any]:
        return await self._request("POST", f"/console/webhooks/{webhook_id}/test", headers=self.console_headers(session_token))

    # /v1 — stable runtime contract
    async def get_number(self) -> tuple[int, Any]:
        return await self._request("GET", f"{OP_API_VERSION}/number")

    async def send_message(self, to: str, body: str, *, idempotency_key: str | None = None) -> tuple[int, Any]:
        payload: dict[str, Any] = {"to": to, "body": body}
        if idempotency_key:
            payload["idempotency_key"] = idempotency_key
        return await self._request("POST", f"{OP_API_VERSION}/messages", json=payload)

    async def list_messages(
        self,
        *,
        direction: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> tuple[int, Any]:
        params: dict[str, Any] = {"limit": limit}
        if direction:
            params["direction"] = direction
        if cursor:
            params["cursor"] = cursor
        return await self._request("GET", f"{OP_API_VERSION}/messages", params=params)

    async def get_message(self, message_id: str) -> tuple[int, Any]:
        return await self._request("GET", f"{OP_API_VERSION}/messages/{message_id}")
