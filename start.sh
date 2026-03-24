#!/bin/bash
# 启动飞书-Kiro 桥接服务（WebSocket 长连接模式）
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# 加载环境变量
if [ -f .env ]; then
    export $(grep -v '^#' .env | grep -v '^\s*$' | xargs)
fi

# 检查必要配置
if [ -z "$FEISHU_APP_ID" ] || [ -z "$FEISHU_APP_SECRET" ]; then
    echo "❌ 请先配置 .env 文件（参考 .env.example）"
    exit 1
fi

echo "🚀 启动飞书-Kiro 桥接服务（WebSocket 长连接，无需公网IP）"
python3 app.py
