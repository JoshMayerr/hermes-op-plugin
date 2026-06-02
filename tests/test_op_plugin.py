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
