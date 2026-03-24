#!/usr/bin/env python3
"""
飞书 Bot ↔ Kiro CLI 桥接服务（WebSocket 长连接版）
使用飞书 SDK 的 WebSocket 模式，无需公网 IP / 端口开放
"""
import os
import json
import time
import logging
import subprocess
import threading

import lark_oapi as lark
from lark_oapi.adapter.flask import *
from lark_oapi.api.im.v1 import *

import re

# ============ 配置 ============
APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
KIRO_TIMEOUT = int(os.environ.get("KIRO_TIMEOUT", "120"))

# ============ 日志 ============
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("feishu-kiro")

# 去重
_processed = set()
_processed_lock = threading.Lock()

# ============ 飞书客户端 ============
client = lark.Client.builder() \
    .app_id(APP_ID) \
    .app_secret(APP_SECRET) \
    .log_level(lark.LogLevel.INFO) \
    .build()


# ============ 飞书消息发送 ============
def reply_message(message_id: str, text: str):
    if len(text) > 4000:
        text = text[:3950] + "\n\n... (内容过长已截断)"
    req = ReplyMessageRequest.builder() \
        .message_id(message_id) \
        .request_body(ReplyMessageRequestBody.builder()
                      .msg_type("text")
                      .content(json.dumps({"text": text}))
                      .build()) \
        .build()
    resp = client.im.v1.message.reply(req)
    if not resp.success():
        log.error(f"回复失败: {resp.code} {resp.msg}")
    else:
        log.info(f"已回复消息 {message_id}")


def strip_ansi(text: str) -> str:
    """去除 ANSI 转义码和终端控制字符"""
    text = re.sub(r'\x1b\[[0-9;]*[a-zA-Z?]', '', text)
    text = re.sub(r'\x1b\].*?\x07', '', text)
    # 去掉 kiro 的启动横幅（ASCII art logo + trust warning + credits）
    lines = text.split('\n')
    clean = []
    skip = False
    for line in lines:
        stripped = line.strip()
        if 'All tools are now trusted' in stripped or 'understand the risks' in stripped:
            continue
        if 'Learn more at' in stripped and 'kiro.dev' in stripped:
            continue
        if 'Credits:' in stripped and 'Time:' in stripped:
            continue
        if '/model' in stripped and 'to change' in stripped:
            continue
        if '/prompts' in stripped or 'Did you know' in stripped:
            continue
        # 跳过 ASCII art（连续的特殊 Unicode 块字符行）
        if stripped and all(c in '⠀⢀⣴⣶⣦⡀⣾⠁⠈⠙⣿⡆⢰⠋⢸⣇⡿⢻⣧⠹⣷⡄⠘⣆⠻⠿⠟⣠⡁⢹⣼⠇⠸⣄⢁⣤⠉⡇⠃⠂⠐⠒⠲⠶⠤⠖⠛⠏⠗⠞⠝⠜⠚⠘⠙⠑⠊⠉⠋⠌⠍⠎⠏⡏⡇⡆⡅⡄⡃⡂⡁⡀⢿⣿⣽⣻⣺⣹⣸⣷⣵⣳⣲⣱⣰⣯⣮⣭⣬⣫⣪⣩⣨⣧⣥⣤⣣⣢⣡⣠⣟⣞⣝⣜⣛⣚⣙⣘⣗⣖⣕⣔⣓⣒⣑⣐⣏⣎⣍⣌⣋⣊⣉⣈⣇⣆⣅⣄⣃⣂╭╮╰╯│─' for c in stripped):
            continue
        clean.append(line)
    # 去掉首尾空行
    text = '\n'.join(clean).strip()
    # 压缩连续空行
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text


# ============ Kiro CLI 调用 ============
def call_kiro(prompt: str) -> str:
    log.info(f"调用 kiro-cli: {prompt[:80]}...")
    try:
        result = subprocess.run(
            ["kiro-cli", "chat", "--no-interactive", "-a", "--wrap", "never", prompt],
            capture_output=True, text=True, timeout=KIRO_TIMEOUT,
            env={**os.environ, "NO_COLOR": "1"},
        )
        output = result.stdout.strip()
        if not output:
            output = result.stderr.strip() or "Kiro 未返回结果"
        return strip_ansi(output)
    except subprocess.TimeoutExpired:
        return f"⏰ Kiro 处理超时（{KIRO_TIMEOUT}s），请简化问题后重试"
    except Exception as e:
        return f"❌ Kiro 调用失败: {e}"


# ============ 异步处理 ============
def handle_user_message(message_id: str, user_text: str):
    reply_message(message_id, "🤖 正在处理，请稍候...")
    kiro_response = call_kiro(user_text)
    reply_message(message_id, kiro_response)


# ============ 事件处理 ============
def on_message_receive(data: P2ImMessageReceiveV1) -> None:
    message = data.event.message
    message_id = message.message_id
    msg_type = message.message_type

    # 去重
    with _processed_lock:
        if message_id in _processed:
            return
        _processed.add(message_id)
        if len(_processed) > 1000:
            _processed.clear()

    # 只处理文本
    if msg_type != "text":
        reply_message(message_id, "目前只支持文本消息哦 📝")
        return

    # 解析文本
    try:
        content = json.loads(message.content or "{}")
        user_text = content.get("text", "").strip()
    except json.JSONDecodeError:
        user_text = ""

    if not user_text:
        return

    # 去掉 @机器人 mention
    if data.event.message.mentions:
        for m in data.event.message.mentions:
            if m.key:
                user_text = user_text.replace(m.key, "").strip()

    if not user_text:
        reply_message(message_id, "请输入您的问题 🤔")
        return

    log.info(f"用户消息: {user_text}")
    t = threading.Thread(target=handle_user_message, args=(message_id, user_text))
    t.daemon = True
    t.start()


# ============ 启动 ============
if __name__ == "__main__":
    if not APP_ID or not APP_SECRET:
        log.error("⚠️  FEISHU_APP_ID / FEISHU_APP_SECRET 未设置")
        exit(1)

    # 注册事件处理器
    handler = lark.EventDispatcherHandler.builder("", "") \
        .register_p2_im_message_receive_v1(on_message_receive) \
        .build()

    # WebSocket 长连接模式启动
    cli = lark.ws.Client(APP_ID, APP_SECRET, event_handler=handler, log_level=lark.LogLevel.INFO)
    log.info("🚀 飞书-Kiro 桥接服务启动（WebSocket 长连接模式，无需公网IP）")
    cli.start()
