#!/usr/bin/env python3
import pytest
from adapters.base import PlatformAdapter, IncomingMessage, OutgoingPayload
from platform_dispatcher import PlatformDispatcher


class FakeAdapter(PlatformAdapter):
    def __init__(self, name):
        self._name = name
        self.sent = []
        self.replies = []

    @property
    def platform(self):
        return self._name

    def start(self):
        pass

    def send_text(self, raw_user_id, text, context_token=None):
        self.sent.append((raw_user_id, text, context_token))

    def reply(self, incoming, payload):
        self.replies.append((incoming, payload))

    def upload_image(self, path):
        return "img_key"

    def upload_file(self, path):
        return "file_key"


def test_register_and_send():
    d = PlatformDispatcher()
    fake = FakeAdapter("feishu")
    d.register(fake)
    d.send("feishu:ou_123", "hello")
    assert len(fake.sent) == 1
    assert fake.sent[0] == ("ou_123", "hello", None)


def test_weixin_send_uses_context_token():
    d = PlatformDispatcher()
    fake = FakeAdapter("weixin")
    fake._context_tokens = {"wxid_abc": "ctx_123"}
    d.register(fake)
    d.send("weixin:wxid_abc", "hello")
    assert fake.sent[0] == ("wxid_abc", "hello", "ctx_123")


def test_unknown_platform_logs_error(caplog):
    import logging
    d = PlatformDispatcher()
    with caplog.at_level(logging.ERROR):
        d.send("telegram:123", "hello")
    assert "未知平台: telegram" in caplog.text
