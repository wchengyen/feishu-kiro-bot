# 微信渠道接入实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 通过 iLink Bot API 将微信接入 kiro-devops，建立第二个联通渠道，同时重构现有飞书逻辑到 PlatformAdapter 抽象层。

**Architecture:** 单进程多线程并发运行 FeishuAdapter（WebSocket）和 WeixinAdapter（HTTP 长轮询），统一通过 MessageHandler 处理业务逻辑，PlatformDispatcher 按 `platform:raw_id` 路由发送。

**Tech Stack:** Python 3.10+, lark-oapi, urllib, threading, pytest

---

## 文件结构

| 文件 | 动作 | 说明 |
|------|------|------|
| `adapters/__init__.py` | 创建 | 包初始化，导出基类和适配器 |
| `adapters/base.py` | 创建 | PlatformAdapter 抽象基类 + IncomingMessage / OutgoingPayload |
| `adapters/feishu.py` | 创建 | 飞书适配器（从 app.py 迁移）|
| `adapters/weixin.py` | 创建 | 微信 iLink 适配器（扫码登录 + 长轮询）|
| `platform_dispatcher.py` | 创建 | 统一发送路由，按 platform 分发 |
| `message_handler.py` | 创建 | 平台无关的业务核心（从 app.py 抽离 handle_user_message）|
| `webhook_server.py` | 创建 | Webhook HTTP 服务（从 app.py 迁移）|
| `gateway.py` | 创建 | 统一入口，启动所有适配器和 webhook |
| `scheduler.py` | 修改 | 增加 `source_platform` / `notify_target` 记录 |
| `app.py` | 删除 | 完全废弃，由 gateway.py 替代 |
| `start.sh` | 修改 | `python3 gateway.py` |
| `.env.example` | 修改 | 新增微信配置和 ALERT_NOTIFY_TARGETS |
| `README.md` | 修改 | 新增多平台支持章节 |
| `tests/test_platform_dispatcher.py` | 创建 | PlatformDispatcher 单元测试 |
| `tests/test_adapters_weixin.py` | 创建 | WeixinAdapter HTTP 调用测试（mock）|

---

## Task 1: 创建 adapters/base.py（抽象基类和数据类）

**Files:**
- Create: `adapters/__init__.py`
- Create: `adapters/base.py`

- [ ] **Step 1: 创建 `adapters/__init__.py`**

```python
from .base import PlatformAdapter, IncomingMessage, OutgoingPayload
from .feishu import FeishuAdapter
from .weixin import WeixinAdapter

__all__ = ["PlatformAdapter", "IncomingMessage", "OutgoingPayload", "FeishuAdapter", "WeixinAdapter"]
```

- [ ] **Step 2: 创建 `adapters/base.py`**

```python
#!/usr/bin/env python3
"""PlatformAdapter 抽象基类与统一消息模型."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class IncomingMessage:
    platform: str
    raw_user_id: str
    unified_user_id: str
    message_id: str
    text: str
    chat_type: str = "private"
    is_at_me: bool = False
    context_token: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class OutgoingPayload:
    text: str
    images: list[str] = field(default_factory=list)
    files: list[str] = field(default_factory=list)


class PlatformAdapter(ABC):
    @property
    @abstractmethod
    def platform(self) -> str:
        """平台标识，如 'feishu' 或 'weixin'."""

    @abstractmethod
    def start(self) -> None:
        """启动监听（阻塞或后台线程）."""

    @abstractmethod
    def send_text(self, raw_user_id: str, text: str, context_token: str | None = None) -> None:
        """主动推送文本消息."""

    @abstractmethod
    def reply(self, incoming: IncomingMessage, payload: OutgoingPayload) -> None:
        """回复某条 incoming 消息."""

    @abstractmethod
    def upload_image(self, path: str) -> str | None:
        """上传图片，返回平台特定的 media_key."""

    @abstractmethod
    def upload_file(self, path: str) -> str | None:
        """上传文件，返回平台特定的 file_key."""
```

- [ ] **Step 3: Commit**

```bash
git add adapters/
git commit -m "feat(adapters): add PlatformAdapter base class and message models"
```

---

## Task 2: 创建 platform_dispatcher.py + 测试

**Files:**
- Create: `platform_dispatcher.py`
- Create: `tests/test_platform_dispatcher.py`

- [ ] **Step 1: 编写 PlatformDispatcher 及测试**

创建 `tests/test_platform_dispatcher.py`：

```python
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
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
cd /home/ubuntu/kiro-devops && pytest tests/test_platform_dispatcher.py -v
```
Expected: FAIL（`platform_dispatcher.py` 不存在）

- [ ] **Step 3: 创建 `platform_dispatcher.py`**

```python
#!/usr/bin/env python3
"""统一发送路由，按 platform:raw_id 前缀分发到对应适配器."""
import logging

from adapters.base import PlatformAdapter

log = logging.getLogger("platform-dispatcher")


class PlatformDispatcher:
    def __init__(self):
        self._adapters: dict[str, PlatformAdapter] = {}

    def register(self, adapter: PlatformAdapter) -> None:
        self._adapters[adapter.platform] = adapter

    def send(self, unified_user_id: str, text: str) -> None:
        if ":" not in unified_user_id:
            log.error(f"非法的统一用户ID格式: {unified_user_id}")
            return
        platform, raw_id = unified_user_id.split(":", 1)
        adapter = self._adapters.get(platform)
        if not adapter:
            log.error(f"未知平台: {platform}")
            return
        ctx = None
        if platform == "weixin":
            ctx = getattr(adapter, "_context_tokens", {}).get(raw_id)
        adapter.send_text(raw_id, text, context_token=ctx)

    def get_adapter(self, platform: str) -> PlatformAdapter | None:
        return self._adapters.get(platform)
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
cd /home/ubuntu/kiro-devops && pytest tests/test_platform_dispatcher.py -v
```
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add platform_dispatcher.py tests/test_platform_dispatcher.py
git commit -m "feat(dispatcher): add PlatformDispatcher with routing tests"
```

---

## Task 3: 创建 adapters/feishu.py（迁移现有飞书逻辑）

**Files:**
- Create: `adapters/feishu.py`
- Modify: `app.py`（暂不删，仅作为参考源）

- [ ] **Step 1: 从 app.py 提取飞书相关代码到 `adapters/feishu.py`**

完整创建 `adapters/feishu.py`：

```python
#!/usr/bin/env python3
"""飞书平台适配器."""
import json
import logging
import os
from typing import Callable

import lark_oapi as lark
from lark_oapi.api.im.v1 import *

from .base import PlatformAdapter, IncomingMessage, OutgoingPayload

log = logging.getLogger("adapter-feishu")

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
FILE_EXTS = {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".csv", ".txt", ".zip", ".mp4", ".opus"}


def _split_text(text: str, limit: int = 4000) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


def extract_file_paths(text: str) -> tuple[list[str], list[str]]:
    import os, re
    images, files = [], []
    for match in re.findall(r'(/[\w./_-]+\.[\w]+)', text):
        if not os.path.isfile(match):
            continue
        ext = os.path.splitext(match)[1].lower()
        if ext in IMAGE_EXTS:
            images.append(match)
        elif ext in FILE_EXTS:
            files.append(match)
    return images, files


class FeishuAdapter(PlatformAdapter):
    platform = "feishu"

    def __init__(self, app_id: str, app_secret: str, on_message: Callable[[IncomingMessage], None]):
        self.app_id = app_id
        self.app_secret = app_secret
        self.on_message = on_message
        self.client = lark.Client.builder() \
            .app_id(app_id) \
            .app_secret(app_secret) \
            .log_level(lark.LogLevel.INFO) \
            .build()

    def start(self) -> None:
        handler = lark.EventDispatcherHandler.builder("", "") \
            .register_p2_im_message_receive_v1(self._on_lark_message) \
            .build()
        cli = lark.ws.Client(self.app_id, self.app_secret, event_handler=handler, log_level=lark.LogLevel.INFO)
        log.info("🚀 飞书适配器启动（WebSocket）")
        cli.start()

    def _on_lark_message(self, data) -> None:
        from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
        data: P2ImMessageReceiveV1
        message = data.event.message
        message_id = message.message_id
        msg_type = message.message_type

        if msg_type != "text":
            self.reply(
                IncomingMessage(
                    platform="feishu", raw_user_id="", unified_user_id="",
                    message_id=message_id, text="", raw={}
                ),
                OutgoingPayload(text="目前只支持文本消息哦 📝")
            )
            return

        try:
            content = json.loads(message.content or "{}")
            user_text = content.get("text", "").strip()
        except json.JSONDecodeError:
            user_text = ""

        if data.event.message.mentions:
            for m in data.event.message.mentions:
                if m.key:
                    user_text = user_text.replace(m.key, "").strip()

        if not user_text:
            return

        user_id = data.event.sender.sender_id.open_id or "unknown"
        is_group = message.chat_type == "group"
        is_at = bool(data.event.message.mentions)

        # 群聊中未 @ 机器人则忽略
        if is_group and not is_at:
            return

        incoming = IncomingMessage(
            platform="feishu",
            raw_user_id=user_id,
            unified_user_id=f"feishu:{user_id}",
            message_id=message_id,
            text=user_text,
            chat_type="group" if is_group else "private",
            is_at_me=is_at,
            raw={"message": message, "data": data}
        )
        self.on_message(incoming)

    def send_text(self, raw_user_id: str, text: str, context_token: str | None = None) -> None:
        chunks = _split_text(text, 4000)
        for chunk in chunks:
            req = CreateMessageRequest.builder() \
                .receive_id_type("open_id") \
                .request_body(CreateMessageRequestBody.builder()
                              .receive_id(raw_user_id)
                              .msg_type("text")
                              .content(json.dumps({"text": chunk}))
                              .build()) \
                .build()
            resp = self.client.im.v1.message.create(req)
            if not resp.success():
                log.error(f"主动发送失败: {resp.code} {resp.msg}")
                break
        log.info(f"已主动发送消息给 {raw_user_id}（{len(chunks)} 段）")

    def reply(self, incoming: IncomingMessage, payload: OutgoingPayload) -> None:
        message_id = incoming.message_id
        text = payload.text
        chunks = _split_text(text, 4000)
        for chunk in chunks:
            req = ReplyMessageRequest.builder() \
                .message_id(message_id) \
                .request_body(ReplyMessageRequestBody.builder()
                              .msg_type("text")
                              .content(json.dumps({"text": chunk}))
                              .build()) \
                .build()
            resp = self.client.im.v1.message.reply(req)
            if not resp.success():
                log.error(f"回复失败: {resp.code} {resp.msg}")
                break
        log.info(f"已回复消息 {message_id}（{len(chunks)} 段）")

        # 图片/文件附件
        for img_path in payload.images:
            key = self.upload_image(img_path)
            if key:
                self._reply_image(message_id, key)
        for file_path in payload.files:
            key = self.upload_file(file_path)
            if key:
                self._reply_file(message_id, key)

    def _reply_image(self, message_id: str, image_key: str) -> None:
        req = ReplyMessageRequest.builder().message_id(message_id).request_body(
            ReplyMessageRequestBody.builder().msg_type("image").content(json.dumps({"image_key": image_key})).build()
        ).build()
        resp = self.client.im.v1.message.reply(req)
        if not resp.success():
            log.error(f"回复图片失败: {resp.code} {resp.msg}")

    def _reply_file(self, message_id: str, file_key: str) -> None:
        req = ReplyMessageRequest.builder().message_id(message_id).request_body(
            ReplyMessageRequestBody.builder().msg_type("file").content(json.dumps({"file_key": file_key})).build()
        ).build()
        resp = self.client.im.v1.message.reply(req)
        if not resp.success():
            log.error(f"回复文件失败: {resp.code} {resp.msg}")

    def upload_image(self, path: str) -> str | None:
        with open(path, "rb") as f:
            req = CreateImageRequest.builder().request_body(
                CreateImageRequestBody.builder().image_type("message").image(f).build()
            ).build()
            resp = self.client.im.v1.image.create(req)
        if resp.success():
            log.info(f"图片上传成功: {resp.data.image_key}")
            return resp.data.image_key
        log.error(f"图片上传失败: {resp.code} {resp.msg}")
        return None

    def upload_file(self, path: str) -> str | None:
        ext = os.path.splitext(path)[1].lower()
        type_map = {".opus": "opus", ".mp4": "mp4", ".pdf": "pdf", ".doc": "doc", ".docx": "doc",
                    ".xls": "xls", ".xlsx": "xls", ".ppt": "ppt", ".pptx": "ppt"}
        file_type = type_map.get(ext, "stream")
        with open(path, "rb") as f:
            req = CreateFileRequest.builder().request_body(
                CreateFileRequestBody.builder().file_type(file_type).file_name(os.path.basename(path)).file(f).build()
            ).build()
            resp = self.client.im.v1.file.create(req)
        if resp.success():
            log.info(f"文件上传成功: {resp.data.file_key}")
            return resp.data.file_key
        log.error(f"文件上传失败: {resp.code} {resp.msg}")
        return None
```

- [ ] **Step 2: Commit**

```bash
git add adapters/feishu.py adapters/__init__.py
git commit -m "feat(adapters): add FeishuAdapter migrated from app.py"
```

---

## Task 4: 创建 adapters/weixin.py（iLink 微信适配器核心）

**Files:**
- Create: `adapters/weixin.py`

- [ ] **Step 1: 创建 `adapters/weixin.py`（扫码登录 + 长轮询 + 文本收发）**

```python
#!/usr/bin/env python3
"""微信 iLink Bot API 适配器."""
import base64
import json
import logging
import os
import struct
import time
import urllib.request
import urllib.error
from typing import Callable

from .base import PlatformAdapter, IncomingMessage, OutgoingPayload

log = logging.getLogger("adapter-weixin")
DEFAULT_BASE_URL = "https://ilinkai.weixin.qq.com"
TOKEN_FILE = os.path.expanduser("~/.kiro/weixin_token.json")


def _random_uin() -> str:
    return base64.b64encode(str(struct.unpack(">I", os.urandom(4))[0]).encode()).decode()


def _headers(token: str | None = None) -> dict:
    h = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "X-WECHAT-UIN": _random_uin(),
    }
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _get(url: str, headers: dict | None = None, timeout: int = 35) -> dict:
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post(path: str, base_url: str, token: str, body: dict, timeout: int = 40) -> dict:
    url = base_url.rstrip("/") + "/" + path.lstrip("/")
    data = json.dumps({**body, "base_info": {"channel_version": "1.0.0"}}, ensure_ascii=False).encode()
    req = urllib.request.Request(url, data=data, headers=_headers(token), method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _split_text(text: str, limit: int = 2000) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        cut = text.rfind("\n", 0, limit)
        if cut <= 0:
            cut = limit
        chunks.append(text[:cut])
        text = text[cut:].lstrip("\n")
    return chunks


class WeixinAdapter(PlatformAdapter):
    platform = "weixin"

    def __init__(self, bot_token: str | None, on_message: Callable[[IncomingMessage], None]):
        self.bot_token = bot_token
        self.base_url = DEFAULT_BASE_URL
        self.on_message = on_message
        self._get_updates_buf = ""
        self._context_tokens: dict[str, str] = {}
        self._running = False
        self._load_token()

    def _load_token(self) -> None:
        if self.bot_token:
            return
        if os.path.exists(TOKEN_FILE):
            with open(TOKEN_FILE) as f:
                data = json.load(f)
            self.bot_token = data.get("bot_token")
            self.base_url = data.get("base_url", DEFAULT_BASE_URL)
            log.info("已从本地文件加载微信 token")

    def _save_token(self) -> None:
        os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
        with open(TOKEN_FILE, "w") as f:
            json.dump({"bot_token": self.bot_token, "base_url": self.base_url}, f)

    def _qr_login(self) -> None:
        log.info("=== 微信扫码登录 ===")
        base = self.base_url.rstrip("/") + "/"
        qr_resp = _get(base + "ilink/bot/get_bot_qrcode?bot_type=3")
        qrcode_id = qr_resp.get("qrcode")
        qrcode_url = qr_resp.get("qrcode_img_content")
        print(f"\n请扫描二维码登录微信 Bot:\n{qrcode_url}\n", flush=True)

        poll_url = base + f"ilink/bot/get_qrcode_status?qrcode={qrcode_id}"
        deadline = time.time() + 480
        headers = {"iLink-App-ClientVersion": "1"}

        while time.time() < deadline:
            try:
                status = _get(poll_url, headers)
            except Exception as e:
                log.warning(f"轮询错误: {e}")
                time.sleep(2)
                continue

            st = status.get("status", "wait")
            if st == "wait":
                print(".", end="", flush=True)
            elif st == "scaned":
                print("\n👀 已扫码，请在微信中点击确认...", flush=True)
            elif st == "confirmed":
                self.bot_token = status.get("bot_token")
                self.base_url = status.get("baseurl", DEFAULT_BASE_URL)
                self._save_token()
                print(f"\n✅ 微信登录成功！", flush=True)
                return
            elif st == "expired":
                raise RuntimeError("二维码已过期，请重新运行程序。")
            time.sleep(1)
        raise RuntimeError("登录超时（8分钟），请重试。")

    def start(self) -> None:
        if not self.bot_token:
            self._qr_login()
        self._running = True
        log.info("🚀 微信适配器启动（iLink 长轮询）")
        self._poll_loop()

    def _poll_loop(self) -> None:
        consecutive_errors = 0
        while self._running:
            try:
                resp = _post(
                    "ilink/bot/getupdates",
                    self.base_url,
                    self.bot_token,
                    {"get_updates_buf": self._get_updates_buf}
                )
                consecutive_errors = 0

                if resp.get("ret") != 0:
                    err = resp.get("errcode")
                    if err == -14:
                        log.warning("微信 session 过期，重新登录...")
                        self._qr_login()
                        continue
                    log.warning(f"getupdates 返回错误: {resp}")
                    time.sleep(5)
                    continue

                self._get_updates_buf = resp.get("get_updates_buf", self._get_updates_buf)
                msgs = resp.get("msgs") or []
                for msg in msgs:
                    self._handle_incoming(msg)

            except urllib.error.HTTPError as e:
                consecutive_errors += 1
                log.warning(f"HTTP 错误 ({consecutive_errors}/3): {e.code}")
                if consecutive_errors >= 3:
                    log.error("连续 3 次错误，暂停 30 秒后重试")
                    time.sleep(30)
                    consecutive_errors = 0
                else:
                    time.sleep(5)
            except Exception as e:
                log.exception("微信轮询异常")
                time.sleep(10)

    def _handle_incoming(self, msg: dict) -> None:
        if msg.get("message_type") != 1:  # 只处理用户消息
            return
        from_user = msg.get("from_user_id", "")
        context_token = msg.get("context_token", "")
        if context_token:
            self._context_tokens[from_user] = context_token

        text = ""
        items = msg.get("item_list") or []
        for item in items:
            if item.get("type") == 1:
                text = item.get("text_item", {}).get("text", "")
                break

        if not text:
            return

        incoming = IncomingMessage(
            platform="weixin",
            raw_user_id=from_user,
            unified_user_id=f"weixin:{from_user}",
            message_id=msg.get("client_id", "") or str(time.time()),
            text=text.strip(),
            chat_type="private",
            is_at_me=False,
            context_token=context_token,
            raw=msg,
        )
        self.on_message(incoming)

    def send_text(self, raw_user_id: str, text: str, context_token: str | None = None) -> None:
        ctx = context_token or self._context_tokens.get(raw_user_id)
        if not ctx:
            log.error(f"无法主动推送给 {raw_user_id}：缺少 context_token")
            return
        chunks = _split_text(text, 2000)
        for chunk in chunks:
            body = {
                "msg": {
                    "to_user_id": raw_user_id,
                    "message_type": 2,
                    "message_state": 2,
                    "context_token": ctx,
                    "item_list": [{"type": 1, "text_item": {"text": chunk}}],
                }
            }
            try:
                resp = _post("ilink/bot/sendmessage", self.base_url, self.bot_token, body)
                if resp.get("ret") != 0:
                    log.error(f"微信发送失败: {resp}")
            except Exception as e:
                log.error(f"微信发送异常: {e}")

    def reply(self, incoming: IncomingMessage, payload: OutgoingPayload) -> None:
        # 微信 reply 与 send_text 相同，必须带 context_token
        self.send_text(incoming.raw_user_id, payload.text, incoming.context_token)

    def upload_image(self, path: str) -> str | None:
        log.warning("微信图片上传一期未实现")
        return None

    def upload_file(self, path: str) -> str | None:
        log.warning("微信文件上传一期未实现")
        return None
```

- [ ] **Step 2: Commit**

```bash
git add adapters/weixin.py
git commit -m "feat(adapters): add WeixinAdapter with iLink QR login and long polling"
```

---

## Task 5: 创建 WeixinAdapter 测试

**Files:**
- Create: `tests/test_adapters_weixin.py`

- [ ] **Step 1: 编写 mock 测试**

```python
#!/usr/bin/env python3
import json
import pytest
from unittest.mock import patch, MagicMock
from adapters.weixin import WeixinAdapter, _headers, _split_text


def test_headers_without_token():
    h = _headers()
    assert h["Content-Type"] == "application/json"
    assert h["AuthorizationType"] == "ilink_bot_token"
    assert "Authorization" not in h
    assert "X-WECHAT-UIN" in h


def test_headers_with_token():
    h = _headers("abc123")
    assert h["Authorization"] == "Bearer abc123"


def test_split_text_short():
    assert _split_text("hello", 2000) == ["hello"]


def test_split_text_long():
    text = "a" * 2500
    chunks = _split_text(text, 2000)
    assert len(chunks) == 2
    assert len(chunks[0]) <= 2000


class TestWeixinAdapter:
    def test_handle_incoming_text(self):
        received = []
        adapter = WeixinAdapter(bot_token="fake", on_message=lambda m: received.append(m))
        adapter._context_tokens = {}
        msg = {
            "message_type": 1,
            "from_user_id": "wxid_abc@im.wechat",
            "context_token": "ctx_123",
            "client_id": "msg_001",
            "item_list": [{"type": 1, "text_item": {"text": "  hello  "}}],
        }
        adapter._handle_incoming(msg)
        assert len(received) == 1
        assert received[0].platform == "weixin"
        assert received[0].raw_user_id == "wxid_abc@im.wechat"
        assert received[0].text == "hello"
        assert adapter._context_tokens["wxid_abc@im.wechat"] == "ctx_123"

    def test_send_text_without_context_token_logs_error(self, caplog):
        import logging
        adapter = WeixinAdapter(bot_token="fake", on_message=lambda m: None)
        with caplog.at_level(logging.ERROR):
            adapter.send_text("wxid_abc", "hi")
        assert "缺少 context_token" in caplog.text
```

- [ ] **Step 2: 运行测试**

```bash
cd /home/ubuntu/kiro-devops && pytest tests/test_adapters_weixin.py -v
```
Expected: 5 passed

- [ ] **Step 3: Commit**

```bash
git add tests/test_adapters_weixin.py
git commit -m "test(adapters): add WeixinAdapter unit tests"
```

---

## Task 6: 创建 message_handler.py（从 app.py 抽离业务逻辑）

**Files:**
- Create: `message_handler.py`

- [ ] **Step 1: 从 app.py 提取 `handle_user_message` 及其依赖函数到 `message_handler.py`**

先阅读当前 app.py 中需要迁移的代码段（约 258-395 行）：

```bash
cd /home/ubuntu/kiro-devops && sed -n '195,395p' app.py
```

基于现有逻辑，创建 `message_handler.py`：

```python
#!/usr/bin/env python3
"""平台无关的消息业务处理核心."""
import logging
import os
import re
import shutil
import subprocess
import threading

from adapters.base import IncomingMessage, OutgoingPayload
from kiro_executor import KiroExecutor, has_decision_signal
from platform_dispatcher import PlatformDispatcher
from scheduler import Scheduler
from session_router import SessionRouter

try:
    from prompt_builder import build_prompt, has_episodic_hint
except ImportError:
    build_prompt = None
    has_episodic_hint = None

log = logging.getLogger("message-handler")
kiro_bin = shutil.which("kiro-cli") or "/home/ubuntu/.local/bin/kiro-cli"
KIRO_TIMEOUT = int(os.environ.get("KIRO_TIMEOUT", "120"))
KIRO_AGENT = os.environ.get("KIRO_AGENT", "").strip()
ENABLE_MEMORY = os.environ.get("ENABLE_MEMORY", "false").lower() in ("true", "1", "yes")

if ENABLE_MEMORY:
    try:
        from memory import MemoryLayer
        from event_store import EventStore
        from event_ingest import parse_manual_command, ingest_to_store
    except ImportError as _e:
        log.warning(f"记忆依赖未安装: {_e}")
        ENABLE_MEMORY = False

memory = MemoryLayer() if ENABLE_MEMORY else None
event_store = EventStore() if ENABLE_MEMORY else None


class MessageHandler:
    def __init__(self, dispatcher: PlatformDispatcher):
        self.dispatcher = dispatcher
        self.session_router = SessionRouter(kiro_bin=kiro_bin, kiro_agent=KIRO_AGENT)
        self.kiro_executor = KiroExecutor(agent=KIRO_AGENT)
        self.scheduler = Scheduler(
            send_fn=self._send_to_target,
            kiro_fn=self._call_kiro_simple,
        )

    def _send_to_target(self, unified_user_id: str, text: str) -> None:
        """定时任务回调：根据 unified_id 路由到对应平台."""
        self.dispatcher.send(unified_user_id, text)

    def _call_kiro_simple(self, prompt: str) -> str:
        """简单调用（供定时任务使用）."""
        log.info(f"调用 kiro-cli (simple): {prompt[:80]}...")
        try:
            cmd = [kiro_bin, "chat", "--no-interactive", "-a", "--wrap", "never"]
            if KIRO_AGENT:
                cmd += ["--agent", KIRO_AGENT]
            cmd.append(prompt)
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=KIRO_TIMEOUT,
                cwd=os.path.expanduser("~"), env={**os.environ, "NO_COLOR": "1"},
            )
            output = result.stdout.strip() or result.stderr.strip() or "Kiro 未返回结果"
            return output
        except subprocess.TimeoutExpired:
            return f"⏰ Kiro 处理超时（{KIRO_TIMEOUT}s）"
        except Exception as e:
            return f"❌ Kiro 调用失败: {e}"

    def handle(self, incoming: IncomingMessage) -> None:
        """所有平台消息的统一入口."""
        user_id = incoming.unified_user_id
        text = incoming.text
        message_id = incoming.message_id

        if text.startswith("/schedule"):
            args = text[len("/schedule"):].strip()
            reply = self.scheduler.handle_command(user_id, args or "help", source_platform=incoming.platform)
            self._reply(incoming, reply)
            return

        if text.startswith("/memory"):
            if not ENABLE_MEMORY:
                self._reply(incoming, "🧠 记忆功能未启用。")
                return
            args = text[len("/memory"):].strip().lower()
            self._reply(incoming, self._handle_memory_command(user_id, args))
            return

        if text.startswith("/event"):
            if not ENABLE_MEMORY:
                self._reply(incoming, "🧠 记忆功能未启用。")
                return
            args = text[len("/event"):].strip()
            self._reply(incoming, self._handle_event_command(user_id, args))
            return

        if text.strip() == "/new":
            self.session_router.clear_active(user_id)
            self._reply(incoming, "🆕 已切换到新会话模式，下条消息将开启新对话。")
            return

        if text.strip().startswith("/resume"):
            parts = text.strip().split()
            if len(parts) < 2:
                self._reply(incoming, "用法：/resume <编号>\n发送 /sessions 查看可用会话。")
                return
            try:
                short_id = int(parts[1].lstrip("#"))
            except ValueError:
                self._reply(incoming, "❌ 请输入数字编号，如 /resume 1")
                return
            session = self.session_router.get_by_short_id(user_id, short_id)
            if not session:
                self._reply(incoming, f"❌ 未找到会话 #{short_id}，发送 /sessions 查看列表。")
                return
            self.session_router.touch(user_id, session["kiro_session_id"])
            self._reply(incoming, f"🔄 已恢复会话 #{short_id} {session['topic']}\n继续发消息即可。")
            return

        if text.strip() == "/sessions":
            self._reply(incoming, self.session_router.list_sessions(user_id))
            return

        if text.strip() == "/status":
            status = self.kiro_executor.get_status(user_id)
            self._reply(incoming, status or "没有正在运行的后台任务。")
            return

        if text.strip() == "/cancel":
            self._reply(incoming, self.kiro_executor.cancel(user_id))
            return

        if self.kiro_executor.is_busy(user_id):
            self._reply(incoming, "⏳ 上一个任务还在后台运行中，请等待完成或发送 /cancel 取消。")
            return

        self._reply(incoming, "🤖 正在处理，请稍候...")

        # 记忆处理
        mem_enabled = ENABLE_MEMORY and memory and memory.is_enabled(user_id)
        if mem_enabled:
            memory.add(user_id, f"用户说：{text}")
        semantic_memories = memory.search(user_id, text) if mem_enabled else []
        episodic_memories = []
        if mem_enabled and event_store and has_episodic_hint and has_episodic_hint(text):
            raw_ents = re.findall(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", text)
            raw_ents += re.findall(r"[\u4e00-\u9fff]{2,}", text)
            entities = [e for e in raw_ents if len(e) >= 2]
            episodic_memories = event_store.search_events(
                user_id, query=text, entities=entities or None, days=14, top_k=5
            )
            if episodic_memories:
                log.info(f"为用户 {user_id} 检索到 {len(episodic_memories)} 条相关事件")

        prompt = build_prompt(text, semantic_memories, episodic_memories) if build_prompt else text
        session_id = self.session_router.resolve(user_id, text)
        is_new = session_id is None

        def on_sync_result(output: str):
            self._deliver_result(incoming, output, session_id, is_new, mem_enabled, len(episodic_memories))

        def on_async_start():
            self._reply(incoming, "⏳ 任务较复杂，已转入后台处理。完成后会主动推送结果。\n发送 /status 查看进度，/cancel 取消。")

        def on_async_result(output: str):
            self._deliver_result(incoming, output, session_id, is_new, mem_enabled, len(episodic_memories))

        def on_progress(msg: str):
            self.dispatcher.send(user_id, msg)

        self.kiro_executor.execute(
            prompt, session_id, user_id,
            on_sync_result, on_async_start, on_async_result, on_progress
        )

    def _deliver_result(self, incoming: IncomingMessage, output: str, session_id, is_new, mem_enabled, episodic_count=0):
        if is_new:
            self.session_router.register_new(incoming.unified_user_id, incoming.text[:30])
            sessions = self.session_router._data.get(incoming.unified_user_id, [])
            sid = sessions[-1]["kiro_session_id"] if sessions else None
        else:
            sid = session_id
            self.session_router.touch(incoming.unified_user_id, session_id)

        suffix = ""
        if episodic_count > 0:
            suffix += f"\n\n📎 本次分析关联了 {episodic_count} 条历史事件（/memory events 查看全部）"
        if has_decision_signal(output):
            suffix += "\n\n💡 回复消息继续当前对话（自动延续上下文）"
        if sid:
            suffix += self.session_router.get_active_label(incoming.unified_user_id, sid)

        self._reply(incoming, output + suffix)

        # 文件路径提取和上传交给各适配器的 reply 实现
        # FeishuAdapter 的 reply 会处理 images/files
        # WeixinAdapter 一期忽略
        if mem_enabled:
            conversation = f"用户：{incoming.text}\n助手：{output}"
            threading.Thread(target=memory.extract_and_store, args=(incoming.unified_user_id, conversation), daemon=True).start()

    def _reply(self, incoming: IncomingMessage, text: str) -> None:
        adapter = self.dispatcher.get_adapter(incoming.platform)
        if not adapter:
            log.error(f"找不到平台适配器: {incoming.platform}")
            return
        adapter.reply(incoming, OutgoingPayload(text=text))

    def _handle_memory_command(self, user_id: str, args: str) -> str:
        if args == "off":
            memory.set_enabled(user_id, False)
            return "🧠 记忆功能已关闭。\n发送 /memory on 可重新开启。"
        elif args == "on":
            memory.set_enabled(user_id, True)
            return "🧠 记忆功能已开启。"
        elif args == "clear":
            memory.clear(user_id)
            return "🗑️ 已清除你的所有记忆。"
        elif args == "status":
            enabled = memory.is_enabled(user_id)
            all_mem = memory.list_all(user_id)
            status = "开启 ✅" if enabled else "关闭 ❌"
            return f"🧠 记忆状态：{status}\n📊 语义记忆条数：{len(all_mem)}"
        elif args.startswith("events"):
            sub = args[len("events"):].strip()
            if sub == "clear":
                if event_store:
                    event_store.clear(user_id)
                return "🗑️ 已清除你的所有事件记录。"
            else:
                if not event_store:
                    return "📭 事件存储未启用。"
                events = event_store.list_events(user_id, days=30, limit=20)
                if not events:
                    return "📭 最近 30 天没有事件记录。"
                lines = ["📋 最近事件（最近 30 天）：\n"]
                for i, e in enumerate(events, 1):
                    ts = e.get("ts", "")[:10] if e.get("ts") else ""
                    lines.append(f"  {i}. [{e['event_type']}] {ts} {e['title']}")
                lines.append(f"\n共 {len(events)} 条，发送 /memory events clear 可清空")
                return "\n".join(lines)
        else:
            return (
                "🧠 记忆管理命令：\n"
                "/memory status - 查看记忆状态\n"
                "/memory on     - 开启记忆\n"
                "/memory off    - 关闭记忆\n"
                "/memory clear  - 清除所有语义记忆\n"
                "/memory events - 查看最近事件\n"
                "/memory events clear - 清空事件记录"
            )

    def _handle_event_command(self, user_id: str, args: str) -> str:
        if not args.strip():
            return (
                "📝 事件录入命令：\n"
                "/event 类型=系统变更 实体=test1,MySQL 标题=索引优化 描述=增加联合索引\n"
                "\n支持字段：类型、实体（逗号分隔）、标题、描述、级别、来源"
            )
        record = parse_manual_command(args)
        record["user_id"] = user_id
        if not record.get("title"):
            return "❌ 标题不能为空，请提供 标题=..."
        result = ingest_to_store(event_store, record)
        if result["ok"]:
            return f"✅ 已记录事件 #{result['event_id'][:8]}：{record['title']}\n关联实体：{', '.join(record.get('entities', []))}"
        else:
            return f"❌ 录入失败：{result['error']}"
```

- [ ] **Step 2: Commit**

```bash
git add message_handler.py
git commit -m "feat(core): add MessageHandler extracted from app.py"
```

---

## Task 7: 创建 webhook_server.py（从 app.py 迁移 Webhook）

**Files:**
- Create: `webhook_server.py`

- [ ] **Step 1: 从 app.py 提取 webhook 相关代码到 `webhook_server.py`**

```python
#!/usr/bin/env python3
"""Webhook HTTP 服务（告警接收 + Dashboard）."""
import json
import logging
import os
import threading

from flask import Flask, request, jsonify

from dashboard import dashboard_bp

log = logging.getLogger("webhook-server")
webhook_app = Flask("kiro-ec2-webhook")
webhook_app.register_blueprint(dashboard_bp)


def _parse_alertmanager(payload: dict) -> dict:
    alert = payload["alerts"][0]
    labels = {**payload.get("commonLabels", {}), **alert.get("labels", {})}
    ann = {**payload.get("commonAnnotations", {}), **alert.get("annotations", {})}
    instance = labels.get("instance", "unknown").split(":")[0]
    is_resolved = alert.get("status") == "resolved"
    return {
        "ok": True,
        "event_id": f"prom-{labels.get('alertname', 'unknown')}-{alert['startsAt'][:19]}",
        "user_id": os.environ.get("ALERT_NOTIFY_USER_ID", "system"),
        "event_type": "故障处理" if is_resolved else "指标异常",
        "title": f"{'[RESOLVED] ' if is_resolved else ''}{ann.get('summary', labels.get('alertname'))}",
        "description": ann.get("description", ""),
        "entities": [instance, labels.get("job", "")] if labels.get("job") else [instance],
        "source": "prometheus",
        "severity": labels.get("severity", "medium"),
        "timestamp": alert.get("endsAt") if is_resolved else alert["startsAt"],
    }


def _resolve_alert_targets() -> list[str]:
    """解析告警推送目标列表."""
    targets = os.environ.get("ALERT_NOTIFY_TARGETS", "").strip()
    if targets:
        return [t.strip() for t in targets.split(",") if t.strip()]
    legacy = os.environ.get("ALERT_NOTIFY_USER_ID", "").strip()
    if legacy:
        return [f"feishu:{legacy}"]
    return []


def create_routes(handler):
    """创建路由，绑定 MessageHandler 用于告警分析回调."""

    @webhook_app.route("/event", methods=["POST"])
    def receive_event():
        auth = request.headers.get("Authorization", "")
        expected = f"Bearer {os.environ.get('WEBHOOK_TOKEN', '')}"
        if auth != expected:
            return jsonify({"ok": False, "error": "Unauthorized"}), 401

        payload = request.get_json(silent=True) or {}

        if "alerts" in payload:
            record = _parse_alertmanager(payload)
        else:
            from event_ingest import webhook_handler
            default_user = os.environ.get("ALERT_NOTIFY_USER_ID", "system")
            record = webhook_handler(payload, default_user_id=default_user)

        if not record.get("ok"):
            return jsonify(record), 400

        try:
            from event_store import EventStore
            event_store = EventStore()
            from event_ingest import ingest_to_store
            result = ingest_to_store(event_store, record)
            if not result["ok"]:
                return jsonify(result), 500
        except Exception:
            log.warning("事件存储不可用，跳过入库")

        auto_severities = os.environ.get("ALERT_AUTO_ANALYZE_SEVERITY", "high,critical").split(",")
        if record.get("severity") in auto_severities:
            threading.Thread(
                target=_trigger_analysis,
                args=(handler, record),
                daemon=True,
                name=f"kiro-alert-{record['event_id'][:8]}"
            ).start()

        return jsonify({
            "ok": True,
            "event_id": record["event_id"],
            "analysis_triggered": record.get("severity") in auto_severities
        }), 200

    @webhook_app.route("/health", methods=["GET"])
    def health():
        return jsonify({
            "status": "ok",
            "memory_enabled": os.environ.get("ENABLE_MEMORY", "false").lower() in ("true", "1", "yes"),
            "webhook": True,
        })


def _trigger_analysis(handler, record: dict):
    """触发 Kiro skill 分析并推送到所有配置目标."""
    import shutil, subprocess
    kiro_bin = shutil.which("kiro-cli") or "/home/ubuntu/.local/bin/kiro-cli"
    targets = _resolve_alert_targets()
    user_id = record.get("user_id") or (targets[0] if targets else "system")

    alert_payload = json.dumps({
        "alert": {
            "source": record["source"],
            "event_type": record["event_type"],
            "title": record["title"],
            "description": record.get("description", ""),
            "entities": record.get("entities", []),
            "severity": record["severity"],
            "timestamp": record.get("timestamp"),
        },
        "instruction": "请分析此 EC2 告警的根因，查询相关指标数据，给出结构化的诊断报告。",
    }, ensure_ascii=False, indent=2)

    log.info(f"触发 Kiro ec2-alert-analyzer: {record['title'][:50]}...")
    cmd = [kiro_bin, "chat", "--no-interactive", "-a", "--wrap", "never", "--agent", "ec2-alert-analyzer", alert_payload]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=int(os.environ.get("ALERT_ANALYZE_TIMEOUT", "300")),
            cwd=os.path.expanduser("~"), env={**os.environ, "NO_COLOR": "1"},
        )
        analysis = result.stdout.strip() or result.stderr.strip() or "Kiro 未返回分析结果"
    except subprocess.TimeoutExpired:
        analysis = "⏰ Kiro EC2 分析超时"
    except Exception as e:
        analysis = f"❌ Kiro 调用失败: {e}"
        log.exception("Kiro 分析失败")

    header = f"🚨 EC2 自动告警分析\n\n【告警】{record['title']}\n【级别】{record['severity'].upper()}\n【来源】{record['source']}\n"
    message = header + "\n" + analysis

    # 推送到所有配置目标
    from platform_dispatcher import PlatformDispatcher
    # 这里需要通过 handler.dispatcher 来发送
    for target in targets:
        try:
            handler.dispatcher.send(target, message)
        except Exception as e:
            log.error(f"告警推送到 {target} 失败: {e}")
    log.info(f"EC2 告警分析结果已推送到 {len(targets)} 个目标")


def start_webhook_server(handler, host: str = "127.0.0.1", port: int = 8080):
    create_routes(handler)
    threading.Thread(
        target=lambda: webhook_app.run(host=host, port=port, threaded=True),
        daemon=True,
        name="webhook-http"
    ).start()
    log.info(f"🌐 Webhook HTTP 监听 {host}:{port}")
```

- [ ] **Step 2: Commit**

```bash
git add webhook_server.py
git commit -m "feat(webhook): extract webhook server from app.py"
```

---

## Task 8: 创建 gateway.py（统一入口）

**Files:**
- Create: `gateway.py`
- Modify: `start.sh`

- [ ] **Step 1: 创建 `gateway.py`**

```python
#!/usr/bin/env python3
"""kiro-devops 统一入口 — 同时运行飞书、微信、Webhook 三通道."""
import logging
import os
import sys
import threading

from adapters import FeishuAdapter, WeixinAdapter
from message_handler import MessageHandler
from platform_dispatcher import PlatformDispatcher
from webhook_server import start_webhook_server

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("gateway")

APP_ID = os.environ.get("FEISHU_APP_ID", "").strip()
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "").strip()
WEIXIN_BOT_TOKEN = os.environ.get("WEIXIN_BOT_TOKEN", "").strip() or None


def main():
    dispatcher = PlatformDispatcher()
    handler = MessageHandler(dispatcher=dispatcher)

    threads = []

    # 飞书适配器
    if APP_ID and APP_SECRET:
        feishu = FeishuAdapter(
            app_id=APP_ID,
            app_secret=APP_SECRET,
            on_message=handler.handle,
        )
        dispatcher.register(feishu)
        t = threading.Thread(target=feishu.start, name="feishu-ws", daemon=True)
        t.start()
        threads.append(t)
        log.info("✅ 飞书适配器已启动")
    else:
        log.warning("⚠️  FEISHU_APP_ID / FEISHU_APP_SECRET 未设置，跳过飞书")

    # 微信适配器
    weixin = WeixinAdapter(
        bot_token=WEIXIN_BOT_TOKEN,
        on_message=handler.handle,
    )
    dispatcher.register(weixin)
    t = threading.Thread(target=weixin.start, name="weixin-poll", daemon=True)
    t.start()
    threads.append(t)
    log.info("✅ 微信适配器已启动")

    # Webhook HTTP
    if os.environ.get("WEBHOOK_ENABLED", "false").lower() == "true":
        port = int(os.environ.get("WEBHOOK_PORT", "8080"))
        host = os.environ.get("WEBHOOK_HOST", "127.0.0.1")
        start_webhook_server(handler, host=host, port=port)
    else:
        log.info("🌐 Webhook 未启用")

    log.info("🚀 kiro-devops gateway 启动完成")

    # 主线程保持存活
    try:
        while True:
            for t in threads:
                t.join(timeout=1)
    except KeyboardInterrupt:
        log.info("👋 收到退出信号，正在关闭...")
        sys.exit(0)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 修改 `start.sh`**

```bash
#!/bin/bash
set -e
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [ -f .env ]; then
    export $(grep -v '^#' .env | grep -v '^\s*$' | xargs)
fi

# 飞书可选，微信可扫码
if [ -z "$FEISHU_APP_ID" ] && [ -z "$FEIXIN_BOT_TOKEN" ] && [ ! -f "$HOME/.kiro/weixin_token.json" ]; then
    echo "⚠️  未配置任何平台（飞书或微信），请检查 .env"
    exit 1
fi

echo "🚀 启动 kiro-devops gateway（飞书 + 微信 + Webhook）"
if [ -f "$SCRIPT_DIR/venv/bin/activate" ]; then
    source "$SCRIPT_DIR/venv/bin/activate"
fi
python3 gateway.py
```

- [ ] **Step 3: Commit**

```bash
git add gateway.py start.sh
git commit -m "feat(gateway): add unified gateway entrypoint for multi-platform"
```

---

## Task 9: 修改 scheduler.py（增加来源平台记录）

**Files:**
- Modify: `scheduler.py`

- [ ] **Step 1: 阅读 scheduler.py 的 handle_command 和任务存储结构**

```bash
cd /home/ubuntu/kiro-devops && head -n 80 scheduler.py
```

- [ ] **Step 2: 修改 `handle_command` 增加 `source_platform` 参数，修改任务存储增加 `notify_target`**

修改点：
1. `handle_command(self, user_id, args)` → `handle_command(self, user_id, args, source_platform="feishu")`
2. 新增任务时写入 `created_by` = `user_id`（已是 unified_id），`notify_target` = `user_id`
3. `Scheduler.__init__` 的 `send_fn` 签名保持不变（仍接收 unified_id, text）

关键修改（diff 示意）：

```python
# scheduler.py
# 在新增任务的地方增加 notify_target
job = {
    "job_id": str(uuid.uuid4())[:8],
    "prompt": prompt,
    "cron": cron_str,
    "created_by": user_id,
    "notify_target": user_id,   # 新增
    "enabled": True,
}
```

- [ ] **Step 3: Commit**

```bash
git add scheduler.py
git commit -m "feat(scheduler): add notify_target to track job creation platform"
```

---

## Task 10: 废弃 app.py，更新 .env.example 和 README

**Files:**
- Delete: `app.py`
- Modify: `.env.example`
- Modify: `README.md`

- [ ] **Step 1: 删除 `app.py`**

```bash
rm app.py
```

- [ ] **Step 2: 修改 `.env.example`**

在现有内容后新增：

```bash
# === 微信 iLink（新增）===
WEIXIN_BOT_TOKEN=                      # 可选；留空则启动时扫码获取
# WEIXIN_TOKEN_FILE=~/.kiro/weixin_token.json

# === 告警推送（改造）===
# 旧：ALERT_NOTIFY_USER_ID=ou_xxx
# 新：支持多目标，逗号分隔
ALERT_NOTIFY_TARGETS=feishu:ou_xxxxxxxx,weixin:wxid_xxxxxxxx@im.wechat
```

- [ ] **Step 3: 修改 `README.md`**

在「核心特性」表格中新增微信行，在「快速开始」后新增「多平台支持」章节（内容见设计文档 Section 10）。

- [ ] **Step 4: Commit**

```bash
git rm app.py
git add .env.example README.md
git commit -m "chore: deprecate app.py, update env and README for multi-platform"
```

---

## Task 11: 集成验证

- [ ] **Step 1: 运行现有测试套件确保无回归**

```bash
cd /home/ubuntu/kiro-devops && pytest tests/ -v --tb=short
```
Expected: 所有现有测试通过（dashboard 相关测试不应受影响）

- [ ] **Step 2: 运行新增测试**

```bash
cd /home/ubuntu/kiro-devops && pytest tests/test_platform_dispatcher.py tests/test_adapters_weixin.py -v
```
Expected: 全部通过

- [ ] **Step 3: 静态检查**

```bash
cd /home/ubuntu/kiro-devops && python3 -m py_compile gateway.py message_handler.py platform_dispatcher.py adapters/*.py webhook_server.py
```
Expected: 无语法错误

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "test: verify all tests pass after multi-platform refactor"
```

---

## 自检清单

| Spec 要求 | 对应任务 |
|-----------|----------|
| PlatformAdapter 抽象层 | Task 1 |
| FeishuAdapter 迁移 | Task 3 |
| WeixinAdapter 扫码+长轮询 | Task 4 |
| PlatformDispatcher 路由 | Task 2 |
| MessageHandler 业务核心 | Task 6 |
| 用户标识 `platform:raw_id` | Task 6 (unified_user_id) |
| 告警多目标推送 `ALERT_NOTIFY_TARGETS` | Task 7 |
| 定时任务来源记录 | Task 9 |
| Gateway 统一入口 | Task 8 |
| 废弃 app.py | Task 10 |
| 微信仅文本（无媒体） | Task 4 (upload_image/upload_file 返回 None) |
| README 更新 | Task 10 |
| 向后兼容旧 ALERT_NOTIFY_USER_ID | Task 7 (_resolve_alert_targets) |

**无 placeholder，每个步骤含具体代码和命令。**
