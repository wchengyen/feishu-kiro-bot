#!/usr/bin/env python3
"""Webhook HTTP 服务（告警接收 + Dashboard）."""
import json
import logging
import os
import re
import shutil
import subprocess
import threading

from flask import Flask, request, jsonify

from dashboard import dashboard_bp

log = logging.getLogger("webhook-server")
webhook_app = Flask("kiro-ec2-webhook")
webhook_app.register_blueprint(dashboard_bp)


def strip_ansi(text: str) -> str:
    """去除 ANSI 转义码和终端控制字符"""
    text = re.sub(r'\x1b\[[0-9;]*[a-zA-Z?]', '', text)
    text = re.sub(r'\x1b\].*?\x07', '', text)
    # 去掉 kiro 的启动横幅（ASCII art logo + trust warning + credits）
    lines = text.split('\n')
    clean = []
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
    kiro_bin = shutil.which("kiro-cli") or "/home/ubuntu/.local/bin/kiro-cli"
    targets = _resolve_alert_targets()

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
        analysis = strip_ansi(result.stdout.strip() or result.stderr.strip() or "Kiro 未返回分析结果")
    except subprocess.TimeoutExpired:
        analysis = "⏰ Kiro EC2 分析超时"
    except Exception as e:
        analysis = f"❌ Kiro 调用失败: {e}"
        log.exception("Kiro 分析失败")

    header = f"🚨 EC2 自动告警分析\n\n【告警】{record['title']}\n【级别】{record['severity'].upper()}\n【来源】{record['source']}\n"
    message = header + "\n" + analysis

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
