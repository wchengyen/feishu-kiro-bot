# Model 选择功能设计文档

## 背景

kiro-devops 当前调用 `kiro-cli` 处理所有聊天和后台任务，但从未显式指定 `--model`，完全依赖 `kiro-cli` 的内部默认选择。本设计引入全局 Model 配置，允许管理员：

1. **初次部署时**通过 `setup.sh` 设定默认模型
2. **运行期间**通过 Dashboard 的 Config 页面修改模型选择

## 设计原则

- **向后兼容**：默认值为空，不传 `--model`，现有行为完全不变
- **全局唯一**：整个 kiro-devops 实例共用同一套模型配置
- **分场景配置**：聊天用 `DEFAULT_MODEL`，后台/告警用 `BACKGROUND_MODEL`，兼顾体验与成本
- **动态列表**：Dashboard 下拉框从 `kiro-cli chat --list-models` 实时获取，不硬编码

---

## 1. 配置层

### 1.1 环境变量

在 `.env.example` 中新增注释：

```bash
# 默认聊天模型（可选，留空使用 kiro-cli 默认）
# DEFAULT_MODEL=
# 后台任务模型（Scheduler / 告警分析，可选，留空使用 kiro-cli 默认）
# BACKGROUND_MODEL=
```

### 1.2 ConfigStore

`dashboard/config_store.py` 的 `CORE_KEYS` 追加两项：

```python
CORE_KEYS = [
    "KIRO_AGENT",
    "ALERT_NOTIFY_USER_ID",
    "ALERT_AUTO_ANALYZE_SEVERITY",
    "WEBHOOK_TOKEN",
    "WEBHOOK_PORT",
    "WEBHOOK_HOST",
    "ENABLE_MEMORY",
    "GROUP_AT_ONLY",
    "DEFAULT_MODEL",      # 新增
    "BACKGROUND_MODEL",   # 新增
]
```

默认值始终为 `""`（空字符串）。读写逻辑完全复用现有的 `.env` 解析器。

---

## 2. Dashboard API 与 UI

### 2.1 新增 API

`GET /api/dashboard/models`

服务端执行：

```bash
kiro-cli chat --list-models --format json
```

返回格式：

```json
{
  "models": [
    {
      "model_id": "deepseek-3.2",
      "description": "Experimental preview of DeepSeek V3.2",
      "rate_multiplier": 0.25,
      "rate_unit": "Credit",
      "context_window_tokens": 164000
    }
  ],
  "default_model": "deepseek-3.2"
}
```

如果 `kiro-cli` 不可用，返回：

```json
{
  "models": [],
  "default_model": null,
  "error": "kiro-cli not found or failed"
}
```

### 2.2 UI 改动

Config 页面的 **Core Config** tab 中，在 `KIRO_AGENT` 字段下方新增两个下拉框：

| 字段 | 标签 | 说明 |
|---|---|---|
| `DEFAULT_MODEL` | 默认聊天模型 | 影响所有用户聊天 |
| `BACKGROUND_MODEL` | 后台任务模型 | 影响 Scheduler 定时任务和 Webhook 告警分析 |

**下拉框行为：**
- 第一个固定选项 `""`，显示为 **"系统默认 — kiro-cli 自动选择"**
- 其余选项从 `/models` API 动态填充，显示格式：`model_id — description`
- 保存复用现有的 `POST /config`，无需新接口
- **降级处理**：若 `/models` 返回 `error`，下拉框退化为普通文本输入框，允许手动填写 `model_id`

---

## 3. 执行层改动

### 3.1 用户聊天 — `kiro_executor.py`

```python
def __init__(self, agent: str = ""):
    self._agent = agent
    self._default_model = os.environ.get("DEFAULT_MODEL", "").strip()
    # ...

def execute(self, prompt, ...):
    cmd = [kiro_bin, "chat", "--no-interactive", "-a", "--trust-tools=execute_bash", "--wrap", "never"]
    if session_id:
        cmd.append("--resume")
    if self._agent:
        cmd += ["--agent", self._agent]
    if self._default_model:
        cmd += ["--model", self._default_model]
    cmd.append(prompt)
    # ...
```

### 3.2 后台定时任务 — `message_handler.py`

```python
def _call_kiro_simple(self, prompt: str) -> str:
    cmd = [kiro_bin, "chat", "--no-interactive", "-a", "--wrap", "never"]
    if KIRO_AGENT:
        cmd += ["--agent", KIRO_AGENT]
    bg_model = os.environ.get("BACKGROUND_MODEL", "").strip()
    if bg_model:
        cmd += ["--model", bg_model]
    cmd.append(prompt)
    # ...
```

### 3.3 告警自动分析 — `webhook_server.py`

```python
def _trigger_analysis(handler, record: dict):
    # ...
    cmd = [kiro_bin, "chat", "--no-interactive", "-a", "--wrap", "never"]
    for tool in tools:
        cmd.append(f"--trust-tools={tool}")
    cmd += ["--agent", agent]
    bg_model = os.environ.get("BACKGROUND_MODEL", "").strip()
    if bg_model:
        cmd += ["--model", bg_model]
    cmd.append(alert_payload)
    # ...
```

**共同原则：** 环境变量为空或仅含空白时不追加 `--model`，完全保持现有行为。

---

## 4. setup.sh 改动

在 `setup_kiro()` 函数中，timeout 和 agent 配置之后，增加 model 选择流程：

```bash
setup_kiro() {
    # ... 现有 timeout / agent 代码 ...

    # ----- Model 选择 -----
    local models_json
    models_json=$(kiro-cli chat --list-models --format json 2>/dev/null || echo "")

    if [ -n "$models_json" ]; then
        local model_list default_id
        model_list=$(echo "$models_json" | python3 -c "
import sys, json
d = json.load(sys.stdin)
for m in d.get('models', []):
    print(m['model_id'])
")
        default_id=$(echo "$models_json" | python3 -c "import sys, json; print(json.load(sys.stdin).get('default_model', ''))")

        echo ""
        echo "可用模型列表："
        echo "  0) 系统默认 (${default_id})"
        local idx=1 map
        map=""
        while IFS= read -r mid; do
            echo "  ${idx}) ${mid}"
            map="${map}${idx}:${mid}\n"
            idx=$((idx + 1))
        done <<< "$model_list"

        # DEFAULT_MODEL
        local current_default choice
        current_default=$(get_env_var "DEFAULT_MODEL" "")
        read -p "选择默认聊天模型 [当前: ${current_default:-系统默认}]: " choice
        if [ -z "$choice" ]; then
            : # 保留当前值
        elif [ "$choice" = "0" ]; then
            update_env_var "DEFAULT_MODEL" ""
        else
            local selected
            selected=$(echo -e "$map" | grep "^${choice}:" | cut -d: -f2)
            [ -n "$selected" ] && update_env_var "DEFAULT_MODEL" "$selected"
        fi

        # BACKGROUND_MODEL
        local current_bg
        current_bg=$(get_env_var "BACKGROUND_MODEL" "")
        read -p "选择后台任务模型 [当前: ${current_bg:-系统默认}]: " choice
        if [ -z "$choice" ]; then
            :
        elif [ "$choice" = "0" ]; then
            update_env_var "BACKGROUND_MODEL" ""
        else
            local selected
            selected=$(echo -e "$map" | grep "^${choice}:" | cut -d: -f2)
            [ -n "$selected" ] && update_env_var "BACKGROUND_MODEL" "$selected"
        fi
    else
        warn "无法获取模型列表，kiro-cli 可能未安装或网络不可用"
        read -p "手动输入默认聊天模型（留空使用系统默认）: " default_model
        [ -n "$default_model" ] && update_env_var "DEFAULT_MODEL" "$default_model"
        read -p "手动输入后台任务模型（留空使用系统默认）: " bg_model
        [ -n "$bg_model" ] && update_env_var "BACKGROUND_MODEL" "$bg_model"
    fi
}
```

**设计要点：**
- 优先通过 `kiro-cli --list-models` 获取带编号菜单
- `0` 号选项表示系统默认（写入空值）
- 回车保留当前值
- `kiro-cli` 不可用时回退到手动输入

---

## 5. 文件变更清单

| 文件 | 改动类型 | 说明 |
|---|---|---|
| `.env.example` | 修改 | 新增 `DEFAULT_MODEL` / `BACKGROUND_MODEL` 注释 |
| `dashboard/config_store.py` | 修改 | `CORE_KEYS` 追加两项 |
| `dashboard/api.py` | 修改 | 新增 `/models` 路由 |
| `dashboard/static/app.js` | 修改 | Core Config tab 新增两个 model 下拉框 |
| `kiro_executor.py` | 修改 | `execute()` 追加 `--model` |
| `message_handler.py` | 修改 | `_call_kiro_simple()` 追加 `--model` |
| `webhook_server.py` | 修改 | `_trigger_analysis()` 追加 `--model` |
| `setup.sh` | 修改 | `setup_kiro()` 增加 model 选择交互 |

---

## 6. 测试建议

1. **单元测试**：`test_config_store.py` 补充 `DEFAULT_MODEL` 和 `BACKGROUND_MODEL` 的读写断言
2. **集成测试**：启动 gateway 后修改 `.env` 中的 model，发送消息验证 `ps aux | grep kiro-cli` 包含 `--model`
3. **Dashboard 测试**：访问 Config 页面，确认 `/models` 返回非空时下拉框正常渲染，返回 error 时降级为文本框
4. **setup.sh 测试**：运行 `./setup.sh` 选择模式 4（仅配置通用项），验证 model 选择交互正常

---

## 7. 未来扩展方向（本设计不实现）

- **按 Agent 绑定模型**：每个 agent 可独立配置 model，覆盖全局默认值
- **用户级临时切换**：聊天中支持 `/model <name>` 覆盖个人偏好
- **模型计费展示**：Dashboard 显示各模型的 `rate_multiplier`，辅助成本决策
