import json
from collections import OrderedDict

import pytest
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if "hermes_plugins" not in sys.modules:
    ns_pkg = types.ModuleType("hermes_plugins")
    ns_pkg.__path__ = []
    ns_pkg.__package__ = "hermes_plugins"
    sys.modules["hermes_plugins"] = ns_pkg
if "hermes_plugins.op" not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        "hermes_plugins.op",
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    module.__package__ = "hermes_plugins.op"
    module.__path__ = [str(ROOT)]
    sys.modules["hermes_plugins.op"] = module
    assert spec and spec.loader
    spec.loader.exec_module(module)

from gateway.config import PlatformConfig

from hermes_plugins.op.adapter import DEDUP_TTL_SECONDS, OPAdapter, _env_enablement, _parse_webhook_port, validate_config
from hermes_plugins.op.op_client import (
    OPClient,
    compute_webhook_signature,
    format_op_error,
    parse_signature_header,
    verify_webhook_signature,
)


class _FakeRequest:
    def __init__(self, payload, headers=None):
        self._raw = json.dumps(payload).encode("utf-8")
        self.headers = headers or {}

    async def read(self):
        return self._raw


def _bare_adapter():
    adapter = OPAdapter.__new__(OPAdapter)
    adapter._webhook_secret = ""
    adapter._insecure_no_signature = True
    adapter._seen_deliveries = OrderedDict()
    adapter._own_number = ""
    adapter._allow_all_numbers = True
    adapter._allowed_numbers = set()
    adapter._background_tasks = set()
    adapter.build_source = lambda **kwargs: kwargs
    adapter.handled = []

    async def handle_message(event):
        adapter.handled.append(event)

    adapter.handle_message = handle_message
    return adapter


def test_op_signature_parse_and_verify():
    raw = b'{"hello":"world"}'
    ts = 1_700_000_000
    sig = compute_webhook_signature("secret", ts, raw)
    header = f"t={ts},v1={sig}"

    parsed = parse_signature_header(header)
    assert parsed is not None
    assert parsed.timestamp == ts
    assert parsed.v1 == sig
    assert verify_webhook_signature("secret", header, raw, now=ts + 10)


def test_op_signature_rejects_stale_timestamp():
    raw = b"{}"
    ts = 1_700_000_000
    header = f"t={ts},v1={compute_webhook_signature('secret', ts, raw)}"

    assert not verify_webhook_signature("secret", header, raw, now=ts + 301)


def test_op_signature_rejects_bad_signature():
    assert not verify_webhook_signature("secret", "t=1700000000,v1=bad", b"{}", now=1_700_000_000)


def test_op_dedupe_cache_detects_duplicates_and_expires():
    adapter = OPAdapter.__new__(OPAdapter)
    adapter._seen_deliveries = OrderedDict()

    assert adapter._is_duplicate_delivery("delivery-1", now=1000) is False
    assert adapter._is_duplicate_delivery("delivery-1", now=1001) is True

    assert adapter._is_duplicate_delivery("delivery-2", "msg-1", now=1002) is False
    assert adapter._is_duplicate_delivery("delivery-3", "msg-1", now=1003) is True
    assert adapter._is_duplicate_delivery("delivery-2", "msg-2", now=1004) is True

    adapter._seen_deliveries["old"] = 1000
    adapter._prune_dedupe(now=1000 + DEDUP_TTL_SECONDS + 1)
    assert "old" not in adapter._seen_deliveries


def test_op_invalid_port_does_not_crash_discovery_or_adapter_init(monkeypatch):
    monkeypatch.setenv("OP_API_KEY", "op_live_test")
    monkeypatch.setenv("OP_WEBHOOK_SECRET", "secret")
    monkeypatch.setenv("OP_WEBHOOK_PORT", "not-a-port")

    assert _parse_webhook_port("not-a-port") is None
    assert _env_enablement()["webhook_port"] is None
    assert validate_config() is False

    adapter = OPAdapter(PlatformConfig())
    assert adapter._webhook_port is None


def test_op_plugin_platform_supports_session_key_value_access(monkeypatch):
    monkeypatch.setenv("OP_API_KEY", "op_live_test")
    monkeypatch.setenv("OP_WEBHOOK_SECRET", "secret")

    adapter = OPAdapter(PlatformConfig())
    source = adapter.build_source(chat_id="+14155551234", user_id="+14155551234")

    assert source.platform == "op"
    assert getattr(source.platform, "value") == "op"


def test_op_validate_config_accepts_valid_signature_config(monkeypatch):
    monkeypatch.setenv("OP_API_KEY", "op_live_test")
    monkeypatch.setenv("OP_WEBHOOK_SECRET", "secret")
    monkeypatch.setenv("OP_WEBHOOK_PORT", "8645")
    monkeypatch.setenv("OP_WEBHOOK_PATH", "webhooks/op")

    assert validate_config() is True


@pytest.mark.asyncio
async def test_op_webhook_dedupes_on_message_id_without_delivery_header():
    adapter = _bare_adapter()
    payload = {
        "id": "msg_replay",
        "direction": "inbound",
        "from": "+14155551234",
        "to": "+14155550000",
        "body": "hello once",
    }

    first = await adapter._handle_webhook(_FakeRequest(payload))
    await next(iter(adapter._background_tasks))
    second = await adapter._handle_webhook(_FakeRequest(payload))

    assert first.status == 200
    assert second.status == 200
    assert json.loads(second.text)["duplicate"] is True
    assert len(adapter.handled) == 1


@pytest.mark.asyncio
async def test_op_info_log_does_not_include_plaintext_body(caplog):
    adapter = _bare_adapter()
    payload = {
        "id": "msg_secret",
        "direction": "inbound",
        "from": "+14155551234",
        "to": "+14155550000",
        "body": "do not log this plaintext",
    }

    with caplog.at_level("INFO", logger="hermes_plugins.op.adapter"):
        response = await adapter._handle_webhook(_FakeRequest(payload, {"op-delivery-id": "delivery-secret"}))
    await next(iter(adapter._background_tasks))

    assert response.status == 200
    info_messages = [record.getMessage() for record in caplog.records if record.levelname == "INFO"]
    assert any("msg_secret" in message and "delivery-secret" in message for message in info_messages)
    assert all("do not log this plaintext" not in message for message in info_messages)


def test_op_extracts_direct_inbound_payload():
    payload = {
        "id": "msg_1",
        "direction": "inbound",
        "from": "+14155551234",
        "to": "+14155550000",
        "body": "hello",
    }

    msg = OPAdapter.extract_inbound_message(payload)
    assert msg == {
        "id": "msg_1",
        "from": "+14155551234",
        "to": "+14155550000",
        "body": "hello",
        "direction": "inbound",
        "message": payload,
        "event_type": "",
    }


def test_op_extracts_enveloped_inbound_payload():
    payload = {
        "type": "message.inbound",
        "data": {
            "message": {
                "message_id": "msg_2",
                "from_number": "+14155551234",
                "to_number": "+14155550000",
                "text": "hello there",
            }
        },
    }

    msg = OPAdapter.extract_inbound_message(payload)
    assert msg is not None
    assert msg["id"] == "msg_2"
    assert msg["from"] == "+14155551234"
    assert msg["body"] == "hello there"


def test_op_ignores_outbound_status_payload():
    payload = {
        "event": "message.status",
        "message": {
            "id": "msg_3",
            "direction": "outbound",
            "status": "sent",
            "from": "+14155550000",
            "to": "+14155551234",
            "body": "reply",
        },
    }

    assert OPAdapter.extract_inbound_message(payload) is None


def test_op_error_formatting():
    body = {"error": {"code": "invalid_phone", "message": "Phone must be E.164.", "field": "phone"}}
    assert format_op_error(400, body) == "OP 400 invalid_phone: Phone must be E.164. (phone)"

def test_register_registers_platform_and_skill(monkeypatch):
    from hermes_plugins.op import register

    calls = {"platform": None, "skill": None}

    class Ctx:
        def register_platform(self, **kwargs):
            calls["platform"] = kwargs

        def register_skill(self, name, path, description=""):
            calls["skill"] = (name, path, description)

    register(Ctx())
    assert calls["platform"]["name"] == "op"
    assert calls["platform"]["label"] == "OP"
    assert calls["platform"]["cron_deliver_env_var"] == "OP_HOME_CHANNEL"
    assert calls["skill"][0] == "op-sms"
    assert calls["skill"][1].name == "SKILL.md"


class _FakeResponse:
    def __init__(self, status=200, payload=None, text=""):
        self.status = status
        self._payload = payload if payload is not None else {}
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def json(self):
        if self._payload == "RAISE_JSON":
            raise ValueError("not json")
        return self._payload

    async def text(self):
        return self._text


class _FakeSession:
    def __init__(self):
        self.calls = []
        self.responses = []

    def queue(self, status=200, payload=None, text=""):
        self.responses.append(_FakeResponse(status, payload, text))

    def _record(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return self.responses.pop(0)

    def post(self, url, **kwargs):
        return self._record("POST", url, **kwargs)

    def get(self, url, **kwargs):
        return self._record("GET", url, **kwargs)

    def patch(self, url, **kwargs):
        return self._record("PATCH", url, **kwargs)

    def delete(self, url, **kwargs):
        return self._record("DELETE", url, **kwargs)


@pytest.mark.asyncio
async def test_op_client_supports_dashboardless_auth_start_and_verify():
    session = _FakeSession()
    session.queue(202, {"verification_id": "ver_1", "expires_in": 600})
    session.queue(200, {"session_token": "sess_1", "bootstrap_key": {"key": "op_live_1"}})
    client = OPClient("", session, api_base="https://api.test")

    assert await client.auth_start("+14155551234") == (202, {"verification_id": "ver_1", "expires_in": 600})
    assert await client.auth_verify("ver_1", "123456") == (200, {"session_token": "sess_1", "bootstrap_key": {"key": "op_live_1"}})

    assert session.calls[0] == (
        "POST",
        "https://api.test/auth/start",
        {"json": {"phone": "+14155551234"}, "headers": {"Content-Type": "application/json", "Accept": "application/json"}},
    )
    assert session.calls[1][1] == "https://api.test/auth/verify"
    assert session.calls[1][2]["json"] == {"verification_id": "ver_1", "code": "123456"}


@pytest.mark.asyncio
async def test_op_client_console_can_create_api_key_and_webhook_with_session_token():
    session = _FakeSession()
    session.queue(201, {"key": "op_live_new", "id": "key_1"})
    session.queue(201, {"id": "wh_1", "secret": "whsec_1"})
    client = OPClient("", session, api_base="https://api.test")

    assert await client.create_api_key("num_1", name="Hermes") == (201, {"key": "op_live_new", "id": "key_1"})
    assert await client.create_webhook(
        "num_1",
        "https://example.com/webhooks/op",
        session_token="sess_1",
    ) == (201, {"id": "wh_1", "secret": "whsec_1"})

    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url == "https://api.test/console/api-keys"
    assert kwargs["json"] == {"number_id": "num_1", "name": "Hermes"}
    method, url, kwargs = session.calls[1]
    assert url == "https://api.test/console/webhooks"
    assert kwargs["headers"]["Authorization"] == "Bearer sess_1"
    assert kwargs["json"]["events"] == ["message.received", "message.sent", "message.failed"]


@pytest.mark.asyncio
async def test_op_client_v1_number_messages_and_idempotency_key():
    session = _FakeSession()
    session.queue(200, {"id": "num_1", "phone": "+14155550000"})
    session.queue(200, {"data": []})
    session.queue(202, {"id": "msg_1"})
    client = OPClient("op_live_test", session, api_base="https://api.test")

    assert await client.get_number() == (200, {"id": "num_1", "phone": "+14155550000"})
    assert await client.list_messages(direction="inbound", limit=20, cursor="cur") == (200, {"data": []})
    assert await client.send_message("+14155551234", "hello", idempotency_key="idem_1") == (202, {"id": "msg_1"})

    assert session.calls[0][1] == "https://api.test/v1/number"
    assert session.calls[1][1] == "https://api.test/v1/messages"
    assert session.calls[1][2]["params"] == {"direction": "inbound", "cursor": "cur", "limit": 20}
    assert session.calls[2][2]["json"] == {"to": "+14155551234", "body": "hello", "idempotency_key": "idem_1"}


def test_op_setup_env_updates_prefers_bootstrap_key_and_webhook_secret():
    from hermes_plugins.op.op_cli import build_env_updates

    verify_payload = {
        "session_token": "sess_1",
        "user": {"phone": "+14155551234", "tier": "free"},
        "bootstrap_key": {"key": "op_live_bootstrap"},
    }
    number_payload = {"id": "num_1", "phone": "+14155550000"}
    webhook_payload = {"id": "wh_1", "secret": "whsec_1"}

    env = build_env_updates(
        verify_payload=verify_payload,
        number_payload=number_payload,
        webhook_payload=webhook_payload,
        allowed_number="+14155551234",
        home_channel="+14155551234",
        webhook_path="/webhooks/op",
        webhook_port=8645,
    )

    assert env["OP_API_KEY"] == "op_live_bootstrap"
    assert env["OP_PHONE_NUMBER"] == "+14155550000"
    assert env["OP_WEBHOOK_SECRET"] == "whsec_1"
    assert env["OP_ALLOWED_NUMBERS"] == "+14155551234"
    assert env["OP_HOME_CHANNEL"] == "+14155551234"


def test_op_update_env_file_preserves_unrelated_values_and_redacts_summary(tmp_path):
    from hermes_plugins.op.op_cli import update_env_file, redact_secret

    env_path = tmp_path / ".env"
    env_path.write_text("EXISTING=1\nOP_API_KEY=old\n", encoding="utf-8")
    update_env_file(env_path, {"OP_API_KEY": "op_live_new", "OP_WEBHOOK_PORT": "8645"})

    assert env_path.read_text(encoding="utf-8") == "EXISTING=1\nOP_API_KEY=op_live_new\nOP_WEBHOOK_PORT=8645\n"
    assert redact_secret("op_live_new") == "op_l…_new"


def test_op_cli_prompts_for_otp_after_auth_start_not_before(monkeypatch):
    import argparse
    from hermes_plugins.op.op_cli import _prompt_missing

    args = argparse.Namespace(phone="+14155551234", code=None)
    monkeypatch.setattr("getpass.getpass", lambda prompt: (_ for _ in ()).throw(AssertionError("OTP prompted too early")))

    _prompt_missing(args)

    assert args.phone == "+14155551234"
    assert args.code is None


def test_register_registers_op_tools_cli_and_all_plugin_skills(monkeypatch):
    from hermes_plugins.op import register

    calls = {"platform": None, "skills": [], "tools": [], "cli": None}

    class Ctx:
        def register_platform(self, **kwargs):
            calls["platform"] = kwargs

        def register_skill(self, name, path, description=""):
            calls["skills"].append((name, path, description))

        def register_tool(self, **kwargs):
            calls["tools"].append(kwargs)

        def register_cli_command(self, **kwargs):
            calls["cli"] = kwargs

    register(Ctx())
    assert calls["platform"]["name"] == "op"
    assert {skill[0] for skill in calls["skills"]} >= {"op-sms", "op-api"}
    assert {tool["name"] for tool in calls["tools"]} >= {"op_send_sms", "op_list_messages", "op_get_number"}
    assert calls["cli"]["name"] == "op"
