#!/usr/bin/env python3
"""
飞书 Bot ↔ Kiro CLI 桥接服务
用户在飞书中 @机器人 发消息 → 本服务接收事件 → 调用 kiro-cli → 回复飞书
"""
import os
import json
import time
import hashlib
import logging
import subprocess
import threading
from flask import Flask, request, jsonify
import requests

# ============ 配置 ============
APP_ID = os.environ.get("FEISHU_APP_ID", "")
APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
VERIFICATION_TOKEN = os.environ.get("FEISHU_VERIFICATION_TOKEN", "")
ENCRYPT_KEY = os.environ.get("FEISHU_ENCRYPT_KEY", "")  # 可选
KIRO_TIMEOUT = int(os.environ.get("KIRO_TIMEOUT", "120"))
PORT = int(os.environ.get("PORT", "9800"))

FEISHU_API = "https://open.feishu.cn/open-apis"

# ============ 日志 ============
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("feishu-kiro")

app = Flask(__name__)

# 去重：记录已处理的 message_id，防止飞书重试导致重复
_processed = set()
_processed_lock = threading.Lock()

# ============ Token 管理 ============
_token_cache = {"token": "", "expire": 0}


def get_tenant_token():
    """获取 tenant_access_token（自动缓存）"""
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expire"] - 60:
        return _token_cache["token"]

    resp = requests.post(f"{FEISHU_API}/auth/v3/tenant_access_token/internal", json={
        "app_id": APP_ID,
        "app_secret": APP_SECRET,
    })
    data = resp.json()
    if data.get("code") != 0:
        log.error(f"获取 token 失败: {data}")
        return ""
    _token_cache["token"] = data["tenant_access_token"]
    _token_cache["expire"] = now + data.get("expire", 7200)
    log.info("tenant_access_token 已刷新")
    return _token_cache["token"]


# ============ 飞书消息发送 ============
def reply_message(message_id, text):
    """回复指定消息"""
    token = get_tenant_token()
    if not token:
        return
    # 飞书单条消息限制，超长截断
    if len(text) > 4000:
        text = text[:3950] + "\n\n... (内容过长已截断)"

    resp = requests.post(
        f"{FEISHU_API}/im/v1/messages/{message_id}/reply",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "msg_type": "text",
            "content": json.dumps({"text": text}),
        },
    )
    result = resp.json()
    if result.get("code") != 0:
        log.error(f"回复失败: {result}")
    else:
        log.info(f"已回复消息 {message_id}")


def send_message(chat_id, text):
    """主动发送消息到群/个人"""
    token = get_tenant_token()
    if not token:
        return
    if len(text) > 4000:
        text = text[:3950] + "\n\n... (内容过长已截断)"

    requests.post(
        f"{FEISHU_API}/im/v1/messages",
        headers={"Authorization": f"Bearer {token}"},
        params={"receive_id_type": "chat_id"},
        json={
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": text}),
        },
    )


# ============ Kiro CLI 调用 ============
def call_kiro(prompt):
    """调用 kiro-cli chat 并返回结果"""
    log.info(f"调用 kiro-cli: {prompt[:80]}...")
    try:
        result = subprocess.run(
            ["kiro-cli", "chat", "--prompt", prompt, "--trust-tools"],
            capture_output=True, text=True, timeout=KIRO_TIMEOUT,
            env={**os.environ, "NO_COLOR": "1"},
        )
        output = result.stdout.strip()
        if not output:
            output = result.stderr.strip() or "Kiro 未返回结果"
        return output
    except subprocess.TimeoutExpired:
        return f"⏰ Kiro 处理超时（{KIRO_TIMEOUT}s），请简化问题后重试"
    except Exception as e:
        return f"❌ Kiro 调用失败: {e}"


# ============ 异步处理 ============
def handle_user_message(message_id, user_text, chat_id):
    """异步处理用户消息：调用 Kiro → 回复飞书"""
    # 先发一个"处理中"提示
    reply_message(message_id, "🤖 正在处理，请稍候...")

    # 调用 Kiro
    kiro_response = call_kiro(user_text)

    # 回复结果
    reply_message(message_id, kiro_response)


# ============ 路由 ============
@app.route("/webhook/event", methods=["POST"])
def event_callback():
    """飞书事件回调入口"""
    payload = request.json
    log.info(f"收到事件: {json.dumps(payload, ensure_ascii=False)[:200]}")

    # 1. URL 验证（首次配置回调地址时飞书会发送 challenge）
    if "challenge" in payload:
        return jsonify({"challenge": payload["challenge"]})

    # 2. 验证 token
    header = payload.get("header", {})
    if VERIFICATION_TOKEN and header.get("token") != VERIFICATION_TOKEN:
        log.warning("verification_token 不匹配，忽略")
        return jsonify({"code": 403}), 403

    # 3. 提取消息
    event = payload.get("event", {})
    message = event.get("message", {})
    message_id = message.get("message_id", "")
    chat_id = message.get("chat_id", "")
    msg_type = message.get("message_type", "")

    # 去重
    with _processed_lock:
        if message_id in _processed:
            return jsonify({"code": 0})
        _processed.add(message_id)
        # 只保留最近 1000 条
        if len(_processed) > 1000:
            _processed.clear()

    # 只处理文本消息
    if msg_type != "text":
        reply_message(message_id, "目前只支持文本消息哦 📝")
        return jsonify({"code": 0})

    # 解析文本内容
    try:
        content = json.loads(message.get("content", "{}"))
        user_text = content.get("text", "").strip()
    except json.JSONDecodeError:
        user_text = ""

    if not user_text:
        return jsonify({"code": 0})

    # 去掉 @机器人 的 mention 标记
    # 飞书格式: @_user_1 实际问题内容
    mentions = event.get("message", {}).get("mentions", [])
    for m in mentions:
        key = m.get("key", "")
        if key:
            user_text = user_text.replace(key, "").strip()

    if not user_text:
        reply_message(message_id, "请输入您的问题 🤔")
        return jsonify({"code": 0})

    log.info(f"用户消息: {user_text}")

    # 异步处理，立即返回 200 给飞书（避免超时）
    t = threading.Thread(target=handle_user_message, args=(message_id, user_text, chat_id))
    t.daemon = True
    t.start()

    return jsonify({"code": 0})


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "ok", "service": "feishu-kiro-bot"})


if __name__ == "__main__":
    log.info(f"飞书-Kiro 桥接服务启动 port={PORT}")
    if not APP_ID or not APP_SECRET:
        log.warning("⚠️  FEISHU_APP_ID / FEISHU_APP_SECRET 未设置，请配置环境变量")
    app.run(host="0.0.0.0", port=PORT, debug=False)
