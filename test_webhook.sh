#!/usr/bin/env bash
# EC2 Webhook 告警接收端到端测试脚本
# 用法: ./test_webhook.sh

BOT_URL="http://127.0.0.1:8080"
TOKEN="kiro-alert-secret-2026"
PASS=0
FAIL=0

pass() { echo "  ✅ $1"; PASS=$((PASS + 1)); }
fail() { echo "  ❌ $1"; FAIL=$((FAIL + 1)); }

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
NC='\033[0m'

echo "═══════════════════════════════════════════════"
echo "  EC2 Webhook 端到端测试"
echo "  Bot URL: ${BOT_URL}"
echo "═══════════════════════════════════════════════"
echo ""

# ── 前置检查 ──
echo "【前置检查】"
if curl -s "${BOT_URL}/health" > /dev/null 2>&1; then
    pass "Bot Webhook 服务可访问"
else
    fail "Bot Webhook 服务不可访问，请确认服务已启动"
    exit 1
fi

# ── 场景 1: 健康检查 ──
echo ""
echo "【场景 1】健康检查 /health"
RESP=$(curl -s "${BOT_URL}/health")
if echo "$RESP" | grep -q '"status":"ok"'; then
    pass "返回状态正常"
else
    fail "健康检查返回异常: $RESP"
fi
if echo "$RESP" | grep -q '"webhook":true'; then
    pass "webhook 功能已启用"
else
    fail "webhook 功能未启用"
fi
if echo "$RESP" | grep -q '"event_store":true'; then
    pass "EventStore 已初始化"
else
    fail "EventStore 未初始化"
fi

# ── 场景 2: Prometheus Critical 告警（触发 Kiro 分析）──
echo ""
echo "【场景 2】Prometheus Critical 告警 → 触发自动分析"
RESP=$(curl -s -X POST "${BOT_URL}/event" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "prom-ec2-cpu-001",
    "event_type": "指标异常",
    "title": "test1 EC2 CPU usage > 80%",
    "description": "CPU utilization is 85.2% for the last 5 minutes",
    "entities": ["test1", "i-0abcd1234"],
    "source": "prometheus",
    "severity": "critical",
    "timestamp": "2026-04-23T10:00:00Z",
    "user_id": "system"
  }')

if echo "$RESP" | grep -q '"ok":true'; then
    pass "HTTP 200，事件接收成功"
else
    fail "事件接收失败: $RESP"
fi
if echo "$RESP" | grep -q '"analysis_triggered":true'; then
    pass "analysis_triggered=true，Kiro skill 分析已触发"
else
    fail "未触发自动分析: $RESP"
fi
if echo "$RESP" | grep -q '"event_id":"prom-ec2-cpu-001"'; then
    pass "event_id 正确返回"
else
    fail "event_id 不匹配"
fi

# ── 场景 3: Alertmanager 原生格式（high）──
echo ""
echo "【场景 3】Alertmanager 原生格式 high 告警"
RESP=$(curl -s -X POST "${BOT_URL}/event" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "version": "4",
    "groupKey": "{}:{alertname=\"HighMemoryUsage\"}",
    "status": "firing",
    "commonLabels": {
      "alertname": "HighMemoryUsage",
      "instance": "test2:9100",
      "job": "node-exporter",
      "severity": "high"
    },
    "commonAnnotations": {
      "summary": "test2 memory usage > 90%",
      "description": "Memory usage is 94.5% for the last 5m"
    },
    "alerts": [{
      "status": "firing",
      "labels": {"instance": "test2:9100", "severity": "high"},
      "annotations": {"summary": "test2 memory usage > 90%"},
      "startsAt": "2026-04-23T11:00:00.000Z"
    }]
  }')

if echo "$RESP" | grep -q '"ok":true'; then
    pass "Alertmanager 格式转换成功"
else
    fail "Alertmanager 格式处理失败: $RESP"
fi
if echo "$RESP" | grep -q '"analysis_triggered":true'; then
    pass "high severity 触发分析"
else
    fail "high severity 未触发分析"
fi

# ── 场景 4: 低级别告警（不触发分析）──
echo ""
echo "【场景 4】low severity 告警（仅入库，不触发分析）"
RESP=$(curl -s -X POST "${BOT_URL}/event" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -d '{
    "id": "prom-ec2-disk-low-001",
    "event_type": "指标异常",
    "title": "test3 disk usage 60%",
    "severity": "low",
    "source": "prometheus",
    "timestamp": "2026-04-23T12:00:00Z"
  }')

if echo "$RESP" | grep -q '"analysis_triggered":false'; then
    pass "low severity 正确跳过自动分析"
else
    fail "low severity 不应触发分析: $RESP"
fi

# ── 场景 5: 鉴权失败 ──
echo ""
echo "【场景 5】错误 Token（应返回 401）"
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST "${BOT_URL}/event" \
  -H "Authorization: Bearer wrong-token" \
  -H "Content-Type: application/json" \
  -d '{"id":"hack","event_type":"指标异常","title":"入侵测试","severity":"critical"}')

if [ "$HTTP_CODE" = "401" ]; then
    pass "鉴权失败返回 401"
else
    fail "期望 401，实际 $HTTP_CODE"
fi

# ── 场景 6: 幂等性测试 ──
echo ""
echo "【场景 6】幂等性（同一 ID 推送两次）"
for i in 1 2; do
  RESP=$(curl -s -X POST "${BOT_URL}/event" \
    -H "Authorization: Bearer ${TOKEN}" \
    -H "Content-Type: application/json" \
    -d '{
      "id": "prom-dup-test-001",
      "event_type": "指标异常",
      "title": "重复推送测试",
      "severity": "low",
      "source": "prometheus",
      "timestamp": "2026-04-23T13:00:00Z"
    }')
done

# 检查数据库中是否只有一条记录
DUP_COUNT=$(python3 -c "import sqlite3; c=sqlite3.connect('events.db').cursor(); c.execute(\"SELECT COUNT(*) FROM events WHERE id='prom-dup-test-001'\"); print(c.fetchone()[0])")
if [ "$DUP_COUNT" = "1" ]; then
    pass "幂等性正确：数据库仅 1 条记录"
else
    fail "幂等性失败：数据库有 $DUP_COUNT 条记录"
fi

# ── 场景 7: 数据库验证 ──
echo ""
echo "【场景 7】数据库事件入库验证"
python3 -c '
import sqlite3
conn = sqlite3.connect("events.db")
c = conn.cursor()
c.execute("SELECT id, event_type, title, severity, source FROM events ORDER BY ts")
rows = c.fetchall()
print("  共入库 " + str(len(rows)) + " 条事件:")
for r in rows:
    print("    - [" + str(r[3]).ljust(8) + "] [" + str(r[4]).ljust(10) + "] " + str(r[2]))
'

# ── 场景 8: 日志验证 ──
echo ""
echo "【场景 8】服务日志检查"
if sudo journalctl -u kiro-devops.service --since "5 minutes ago" --no-pager 2>/dev/null | grep -q "触发 Kiro ec2-alert-analyzer skill"; then
    pass "日志中发现 Kiro skill 触发记录"
else
    fail "日志中未找到 Kiro skill 触发记录"
fi
if sudo journalctl -u kiro-devops.service --since "5 minutes ago" --no-pager 2>/dev/null | grep -q "事件入库"; then
    pass "日志中发现事件入库记录"
else
    fail "日志中未找到事件入库记录"
fi

# ── 汇总 ──
echo ""
echo "═══════════════════════════════════════════════"
echo "  测试完成"
echo "═══════════════════════════════════════════════"
printf "${GREEN}通过: %d${NC}\n" "$PASS"
printf "${RED}失败: %d${NC}\n" "$FAIL"

if [ "$FAIL" -eq 0 ]; then
    echo ""
    echo "🎉 所有测试通过！"
    exit 0
else
    echo ""
    echo "⚠️  存在失败项，请检查日志排查"
    exit 1
fi
