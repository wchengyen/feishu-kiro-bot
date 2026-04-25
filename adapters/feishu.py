#!/usr/bin/env python3
"""飞书平台适配器."""
import json
import logging
import os
import re
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
    @property
    def platform(self) -> str:
        return "feishu"

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
