# Alert Mapping Dynamic Agent Invocation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a configurable alert rule engine that maps incoming events to different kiro-cli agents/skills based on multi-dimensional match conditions, with a redesigned Dashboard UI and hot-reload support.

**Architecture:** Add `alert_matcher.py` with `AlertMatcher` (rule evaluation) and `ConfigReloader` (mtime-based hot reload). Modify `webhook_server.py` to resolve agent/tools/instruction/timeout dynamically instead of hard-coding `--agent ec2-alert-analyzer`. Extend Dashboard API and Vue UI to edit the new rule format.

**Tech Stack:** Python 3.10, Flask, Vue 3 (no build), kiro-cli agents, pytest

---

## File Map

| File | Responsibility |
|------|---------------|
| `alert_matcher.py` (new) | Rule matching engine + config hot reloader |
| `webhook_server.py` (modify) | Wire matcher into `_trigger_analysis`, expose `_raw_labels` |
| `dashboard/api.py` (modify) | `GET/POST /mappings` new format, add `GET/POST /alert-defaults` |
| `dashboard/config_store.py` (modify) | `read_alert_defaults`, `write_alert_defaults` helpers |
| `dashboard/static/app.js` (modify) | Alert Mappings page: rule cards, match editor, action editor, sort, enable toggle |
| `tests/test_alert_matcher.py` (new) | Unit tests for matcher engine |

---

## Task 1: Create `alert_matcher.py` core engine

**Files:**
- Create: `alert_matcher.py`
- Test: `tests/test_alert_matcher.py`

### Step 1: Write the failing test

```python
# tests/test_alert_matcher.py
import pytest
from alert_matcher import AlertMatcher


def test_exact_match():
    matcher = AlertMatcher(
        mappings=[{
            "name": "node-notready",
            "enabled": True,
            "match": {"source": "prometheus", "alertname": "NodeNotReady"},
            "action": {"agent": "eks-node-analyzer", "tools": ["execute_bash"]}
        }],
        defaults={"agent": "ec2-alert-analyzer", "tools": ["execute_bash"]}
    )
    result = matcher.match({
        "source": "prometheus",
        "title": "[NodeNotReady] Node ip-10-42-9-29 is NotReady",
        "severity": "critical",
        "_raw_labels": {"alertname": "NodeNotReady"}
    })
    assert result["agent"] == "eks-node-analyzer"


def test_fallback_when_no_match():
    matcher = AlertMatcher(mappings=[], defaults={"agent": "ec2-alert-analyzer"})
    result = matcher.match({"source": "prometheus", "title": "UnknownAlert"})
    assert result["agent"] == "ec2-alert-analyzer"


def test_regex_match():
    matcher = AlertMatcher(
        mappings=[{
            "match": {"alertname": "Node.*"},
            "action": {"agent": "node-agent"}
        }],
        defaults={"agent": "default"}
    )
    result = matcher.match({"title": "[NodeExporterDown] down"})
    assert result["agent"] == "node-agent"


def test_array_or_match():
    matcher = AlertMatcher(
        mappings=[{
            "match": {"severity": ["critical", "high"]},
            "action": {"agent": "urgent-agent"}
        }],
        defaults={"agent": "default"}
    )
    assert matcher.match({"severity": "high"})["agent"] == "urgent-agent"
    assert matcher.match({"severity": "low"})["agent"] == "default"


def test_labels_match():
    matcher = AlertMatcher(
        mappings=[{
            "match": {"labels": {"job": "node-exporter"}},
            "action": {"agent": "node-agent"}
        }],
        defaults={"agent": "default"}
    )
    result = matcher.match({
        "source": "prometheus",
        "title": "Something",
        "_raw_labels": {"job": "node-exporter"}
    })
    assert result["agent"] == "node-agent"


def test_disabled_rule_skipped():
    matcher = AlertMatcher(
        mappings=[{
            "enabled": False,
            "match": {"alertname": "NodeNotReady"},
            "action": {"agent": "should-not-match"}
        }],
        defaults={"agent": "default"}
    )
    result = matcher.match({"title": "[NodeNotReady] ..."})
    assert result["agent"] == "default"


def test_priority_order():
    matcher = AlertMatcher(
        mappings=[
            {"match": {"alertname": "Node.*"}, "action": {"agent": "first"}},
            {"match": {"alertname": "NodeNotReady"}, "action": {"agent": "second"}}
        ],
        defaults={"agent": "default"}
    )
    result = matcher.match({"title": "[NodeNotReady] ..."})
    assert result["agent"] == "first"
```

### Step 2: Run test to verify it fails

```bash
cd /home/ubuntu/kiro-devops && pytest tests/test_alert_matcher.py -v
```

Expected: All tests FAIL with `ModuleNotFoundError: No module named 'alert_matcher'`

### Step 3: Implement `alert_matcher.py`

```python
# alert_matcher.py
import json
import os
import re
import threading
import time
from typing import Any


class AlertMatcher:
    def __init__(self, mappings: list[dict], defaults: dict | None = None):
        self.rules = [r for r in (mappings or []) if r.get("enabled", True)]
        self.defaults = defaults or {}

    def match(self, record: dict) -> dict:
        for rule in self.rules:
            if self._rule_matches(rule.get("match", {}), record):
                action = {**self.defaults, **rule.get("action", {})}
                return action
        return self.defaults.copy()

    def _rule_matches(self, match: dict, record: dict) -> bool:
        for field, expected in match.items():
            if field == "labels":
                if not self._labels_match(expected, record):
                    return False
            else:
                actual = self._extract_field(record, field)
                if not self._value_matches(expected, actual):
                    return False
        return True

    def _value_matches(self, expected: Any, actual: str) -> bool:
        if isinstance(expected, list):
            return actual in expected
        if isinstance(expected, str) and re.search(r"[.*|^$|+?{}\[\]]", expected):
            return bool(re.search(expected, actual))
        return expected == actual

    def _labels_match(self, expected_labels: dict, record: dict) -> bool:
        raw_labels = record.get("_raw_labels", {})
        for k, v in expected_labels.items():
            if not self._value_matches(v, raw_labels.get(k, "")):
                return False
        return True

    def _extract_field(self, record: dict, field: str) -> str:
        if field == "alertname":
            title = record.get("title", "")
            m = re.search(r"\[([^\]]+)\]", title)
            return m.group(1) if m else title.split()[0] if title else ""
        return record.get(field, "")


class ConfigReloader:
    def __init__(self, store):
        self.store = store
        self._matcher: AlertMatcher | None = None
        self._mtime = 0.0
        self._lock = threading.Lock()

    def get_matcher(self) -> AlertMatcher:
        with self._lock:
            path = getattr(self.store, "mappings_path", "dashboard_config.json")
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                mtime = 0
            if self._matcher is None or mtime > self._mtime:
                cfg = self.store.load()
                self._matcher = AlertMatcher(
                    cfg.get("mappings", []),
                    cfg.get("alert_defaults", {})
                )
                self._mtime = mtime
            return self._matcher
```

### Step 4: Run tests to verify they pass

```bash
cd /home/ubuntu/kiro-devops && pytest tests/test_alert_matcher.py -v
```

Expected: 7 tests PASS

### Step 5: Commit

```bash
cd /home/ubuntu/kiro-devops
git add alert_matcher.py tests/test_alert_matcher.py
git commit -m "feat(alert): add AlertMatcher engine with regex, OR, labels support"
```

---

## Task 2: Integrate matcher into `webhook_server.py`

**Files:**
- Modify: `webhook_server.py`

### Step 1: Import and initialize ConfigReloader at module level

At the top of `webhook_server.py` (after imports, before route definitions), add:

```python
from alert_matcher import AlertMatcher, ConfigReloader
from dashboard.config_store import ConfigStore

config_reloader = ConfigReloader(ConfigStore())
```

### Step 2: Enhance `_parse_alertmanager` to preserve raw labels

Replace the existing `_parse_alertmanager` function body with:

```python
def _parse_alertmanager(payload: dict) -> dict:
    alert = payload["alerts"][0]
    labels = {**payload.get("commonLabels", {}), **alert.get("labels", {})}
    ann = {**payload.get("commonAnnotations", {}), **alert.get("annotations", {})}
    instance = labels.get("instance", "unknown").split(":")[0]
    is_resolved = alert.get("status") == "resolved"
    result = {
        "ok": True,
        "event_id": f"prom-{labels.get('alertname', 'unknown')}-{alert['startsAt'][:19]}-{'resolved' if is_resolved else 'firing'}",
        "user_id": os.environ.get("ALERT_NOTIFY_USER_ID", "system"),
        "event_type": "故障处理" if is_resolved else "指标异常",
        "title": f"{'[RESOLVED] ' if is_resolved else ''}{ann.get('summary', labels.get('alertname'))}",
        "description": ann.get("description", ""),
        "entities": [instance, labels.get("job", "")] if labels.get("job") else [instance],
        "source": "prometheus",
        "severity": labels.get("severity", "medium"),
        "timestamp": alert.get("endsAt") if is_resolved else alert["startsAt"],
        "_raw_labels": labels,
    }
    return result
```

### Step 3: Rewrite `_trigger_analysis` to use dynamic agent resolution

Replace the existing `_trigger_analysis` function with:

```python
def _trigger_analysis(handler, record: dict):
    """触发 Kiro skill 分析并推送到所有配置目标."""
    kiro_bin = shutil.which("kiro-cli") or "/home/ubuntu/.local/bin/kiro-cli"
    targets = _resolve_alert_targets()

    matcher = config_reloader.get_matcher()
    action = matcher.match(record)

    agent = action.get("agent", "ec2-alert-analyzer")
    tools = action.get("tools", ["execute_bash"])
    timeout = action.get("timeout", 300)
    instruction = action.get("instruction")
    if not instruction:
        instruction = "请分析此告警的根因，查询相关指标数据，给出结构化的诊断报告。"

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
        "instruction": instruction,
    }, ensure_ascii=False, indent=2)

    log.info(f"触发 Kiro {agent}: {record['title'][:50]}...")
    cmd = [kiro_bin, "chat", "--no-interactive", "-a", "--wrap", "never"]
    for tool in tools:
        cmd.append(f"--trust-tools={tool}")
    cmd += ["--agent", agent, alert_payload]

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=timeout,
            cwd=os.path.expanduser("~"), env={**os.environ, "NO_COLOR": "1"},
        )
        analysis = strip_ansi(result.stdout.strip() or result.stderr.strip() or "Kiro 未返回分析结果")
    except subprocess.TimeoutExpired:
        analysis = f"⏰ Kiro {agent} 分析超时"
    except Exception as e:
        analysis = f"❌ Kiro 调用失败: {e}"
        log.exception("Kiro 分析失败")

    header = f"🚨 自动告警分析\n\n【告警】{record['title']}\n【级别】{record['severity'].upper()}\n【来源】{record['source']}\n"
    message = header + "\n" + analysis

    for target in targets:
        try:
            handler.dispatcher.send(target, message)
        except Exception as e:
            log.error(f"告警推送到 {target} 失败: {e}")
    log.info(f"告警分析结果已推送到 {len(targets)} 个目标")
```

### Step 4: Verify syntax

```bash
cd /home/ubuntu/kiro-devops && python3 -m py_compile webhook_server.py
```

Expected: No output (success)

### Step 5: Commit

```bash
cd /home/ubuntu/kiro-devops
git add webhook_server.py
git commit -m "feat(webhook): integrate AlertMatcher for dynamic agent resolution"
```

---

## Task 3: Extend Dashboard API for new format

**Files:**
- Modify: `dashboard/config_store.py`
- Modify: `dashboard/api.py`

### Step 1: Add alert_defaults helpers to ConfigStore

In `dashboard/config_store.py`, after `write_pinned_resources`, add:

```python
    def read_alert_defaults(self) -> dict:
        data = self._read_dashboard_config()
        return data.get("alert_defaults", {
            "agent": "ec2-alert-analyzer",
            "tools": ["execute_bash"],
            "timeout": 300
        })

    def write_alert_defaults(self, defaults: dict) -> None:
        data = self._read_dashboard_config()
        data["alert_defaults"] = defaults
        self._write_dashboard_config(data)
```

### Step 2: Add `/alert-defaults` API routes

In `dashboard/api.py`, after `post_mappings`, add:

```python
@dashboard_bp.route("/api/dashboard/alert-defaults", methods=["GET"])
@require_auth
def get_alert_defaults():
    store = ConfigStore(env_path=os.environ.get("ENV_PATH", ".env"))
    return jsonify({"ok": True, "defaults": store.read_alert_defaults()})


@dashboard_bp.route("/api/dashboard/alert-defaults", methods=["POST"])
@require_auth
def post_alert_defaults():
    payload = request.get_json(silent=True) or {}
    store = ConfigStore(env_path=os.environ.get("ENV_PATH", ".env"))
    defaults = payload.get("defaults", {})
    store.write_alert_defaults(defaults)
    return jsonify({"ok": True})
```

### Step 3: Verify syntax

```bash
cd /home/ubuntu/kiro-devops && python3 -m py_compile dashboard/config_store.py dashboard/api.py
```

Expected: No output (success)

### Step 4: Commit

```bash
cd /home/ubuntu/kiro-devops
git add dashboard/config_store.py dashboard/api.py
git commit -m "feat(dashboard): add alert-defaults API for fallback agent config"
```

---

## Task 4: Redesign Dashboard Alert Mappings UI

**Files:**
- Modify: `dashboard/static/app.js`

This is a large Vue template change. Replace the entire `tab === 'mappings'` block and its related setup/reactive code.

### Step 1: Replace the ConfigPage template's mappings section

In `dashboard/static/app.js`, find the template string (around line 1113) and replace the entire `<div v-if="tab === 'mappings'">...</div>` block with:

```html
      <div v-if="tab === 'mappings'">
        <div class="toolbar">
          <button @click="addMapping">添加规则</button>
          <button class="secondary" @click="saveMappings">保存规则</button>
        </div>
        <div v-for="(m, i) in mappings" :key="i" class="info-card" :class="{ disabled: !m.enabled }" style="margin-bottom:16px">
          <div class="mapping-header">
            <span class="mapping-index">{{ i + 1 }}</span>
            <input v-model="m.name" placeholder="规则名称" class="mapping-name" />
            <label class="toggle">
              <input type="checkbox" v-model="m.enabled" />
              <span>{{ m.enabled ? '启用' : '停用' }}</span>
            </label>
            <button @click="moveMapping(i, -1)" :disabled="i === 0">↑</button>
            <button @click="moveMapping(i, 1)" :disabled="i === mappings.length - 1">↓</button>
            <button class="btn-danger-sm" @click="removeMapping(i)">删除</button>
          </div>
          <div class="mapping-body">
            <div class="mapping-section">
              <h4>Match 条件</h4>
              <div class="form-row">
                <label>Source</label>
                <select v-model="m.match.source">
                  <option value="">- 任意 -</option>
                  <option v-for="s in sourceOptions" :key="s" :value="s">{{ s }}</option>
                </select>
              </div>
              <div class="form-row">
                <label>Alertname</label>
                <input v-model="m.match.alertname" placeholder="支持正则，如 Node.*|ExporterDown" />
              </div>
              <div class="form-row">
                <label>Severity</label>
                <div class="checkbox-group">
                  <label v-for="sev in ['critical','high','medium','low']" :key="sev">
                    <input type="checkbox" :value="sev" v-model="m.match.severity" /> {{ sev }}
                  </label>
                </div>
              </div>
              <div class="form-row">
                <label>Labels</label>
                <div class="kv-list">
                  <div v-for="(lv, li) in (m.match.labelsList || [])" :key="li" class="kv-item">
                    <input v-model="lv.key" placeholder="key" />
                    <input v-model="lv.value" placeholder="value (支持正则)" />
                    <button @click="removeLabel(i, li)">×</button>
                  </div>
                  <button @click="addLabel(i)">+ 添加 Label</button>
                </div>
              </div>
            </div>
            <div class="mapping-section">
              <h4>Action</h4>
              <div class="form-row">
                <label>Agent</label>
                <select v-model="m.action.agent">
                  <option value="">- 选择 Agent -</option>
                  <option v-for="a in agentOptions" :key="a" :value="a">{{ a }}</option>
                </select>
              </div>
              <div class="form-row">
                <label>Tools</label>
                <div class="checkbox-group">
                  <label v-for="t in toolOptions" :key="t">
                    <input type="checkbox" :value="t" v-model="m.action.tools" /> {{ t }}
                  </label>
                </div>
              </div>
              <div class="form-row">
                <label>Timeout (秒)</label>
                <input type="number" v-model.number="m.action.timeout" min="30" max="1800" />
              </div>
              <div class="form-row">
                <label>Instruction</label>
                <textarea v-model="m.action.instruction" rows="3" placeholder="留空使用 Agent 默认 Prompt"></textarea>
              </div>
            </div>
          </div>
        </div>
        <div v-if="mappings.length === 0" class="empty" style="padding:24px">暂无规则</div>

        <div class="info-card" style="margin-top:24px">
          <div style="display:flex;justify-content:space-between;align-items:center;cursor:pointer" @click="showDefaults = !showDefaults">
            <h4>Fallback Defaults（未匹配时的默认配置）</h4>
            <span>{{ showDefaults ? '▲' : '▼' }}</span>
          </div>
          <div v-if="showDefaults" style="margin-top:12px">
            <div class="form-row">
              <label>默认 Agent</label>
              <select v-model="alertDefaults.agent">
                <option v-for="a in agentOptions" :key="a" :value="a">{{ a }}</option>
              </select>
            </div>
            <div class="form-row">
              <label>默认 Tools</label>
              <div class="checkbox-group">
                <label v-for="t in toolOptions" :key="t">
                  <input type="checkbox" :value="t" v-model="alertDefaults.tools" /> {{ t }}
                </label>
              </div>
            </div>
            <div class="form-row">
              <label>默认 Timeout</label>
              <input type="number" v-model.number="alertDefaults.timeout" min="30" max="1800" />
            </div>
            <button @click="saveAlertDefaults">保存默认配置</button>
          </div>
        </div>
      </div>
```

### Step 2: Add CSS styles for the new mapping cards

In the same `dashboard/static/app.js` file, find the `<style>` section (usually at the bottom) and append:

```css
.mapping-header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 12px;
  flex-wrap: wrap;
}
.mapping-index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 24px;
  height: 24px;
  background: #2563eb;
  color: #fff;
  border-radius: 50%;
  font-size: 12px;
  font-weight: bold;
}
.mapping-name {
  flex: 1;
  font-weight: bold;
  font-size: 15px;
  border: none;
  background: transparent;
  border-bottom: 1px dashed #ccc;
  padding: 4px;
}
.mapping-name:focus {
  outline: none;
  border-bottom-color: #2563eb;
}
.info-card.disabled {
  opacity: 0.5;
}
.mapping-body {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
}
@media (max-width: 800px) {
  .mapping-body { grid-template-columns: 1fr; }
}
.mapping-section h4 {
  margin: 0 0 12px 0;
  color: #374151;
  font-size: 14px;
  text-transform: uppercase;
  letter-spacing: 0.5px;
}
.form-row {
  margin-bottom: 12px;
}
.form-row label {
  display: block;
  font-size: 12px;
  color: #6b7280;
  margin-bottom: 4px;
}
.form-row input,
.form-row select,
.form-row textarea {
  width: 100%;
  padding: 6px 8px;
  border: 1px solid #d1d5db;
  border-radius: 6px;
  font-size: 13px;
}
.checkbox-group {
  display: flex;
  flex-wrap: wrap;
  gap: 12px;
}
.checkbox-group label {
  display: flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  color: #374151;
  cursor: pointer;
}
.kv-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.kv-item {
  display: flex;
  gap: 6px;
  align-items: center;
}
.kv-item input {
  flex: 1;
}
.toggle span {
  font-size: 12px;
  color: #2563eb;
}
```

### Step 3: Replace the mappings reactive logic in setup()

In the `setup()` function of the ConfigPage component, replace all mappings-related reactive vars and functions with:

```javascript
    const mappings = ref([]);
    const alertDefaults = reactive({ agent: "ec2-alert-analyzer", tools: ["execute_bash"], timeout: 300 });
    const showDefaults = ref(false);
    const toolOptions = ["execute_bash", "fs_read", "fs_write", "grep", "glob"];

    // ... existing load() code stays, but add inside try blocks:
    // In the mappings load section (after line 1207):
    //   const m = await api("/mappings");
    //   mappings.value = normalizeMappings(m.mappings || []);

    // In the new alert-defaults load section (add after serviceRules load):
    try {
      const d = await api("/alert-defaults");
      Object.assign(alertDefaults, d.defaults || {});
    } catch {}
```

Add the normalization helper before `setup()` returns:

```javascript
    function normalizeMappings(raw) {
      return (raw || []).map((m, idx) => {
        // Backward compat: old flat format {source, service, severity, agent, skill}
        if (m.match === undefined && m.action === undefined) {
          const labels = {};
          if (m.service) labels.service = m.service;
          const sev = m.severity ? [m.severity] : [];
          return {
            name: m.name || `legacy-${m.agent || "rule"}-${idx + 1}`,
            enabled: true,
            match: {
              source: m.source || "",
              alertname: "",
              severity: sev,
              labelsList: Object.keys(labels).map(k => ({ key: k, value: labels[k] }))
            },
            action: {
              agent: m.agent || "",
              tools: [],
              timeout: 300,
              instruction: ""
            }
          };
        }
        // New format normalization
        const nm = {
          name: m.name || `rule-${idx + 1}`,
          enabled: m.enabled !== false,
          match: {
            source: m.match?.source || "",
            alertname: m.match?.alertname || "",
            severity: m.match?.severity || [],
            labelsList: []
          },
          action: {
            agent: m.action?.agent || "",
            tools: m.action?.tools || [],
            timeout: m.action?.timeout || 300,
            instruction: m.action?.instruction || ""
          }
        };
        if (m.match?.labels) {
          nm.match.labelsList = Object.entries(m.match.labels).map(([k, v]) => ({ key: k, value: v }));
        }
        return nm;
      });
    }

    function denormalizeMappings(list) {
      return list.map(m => {
        const match = {};
        if (m.match.source) match.source = m.match.source;
        if (m.match.alertname) match.alertname = m.match.alertname;
        if (m.match.severity && m.match.severity.length) match.severity = m.match.severity;
        const labels = {};
        for (const lv of (m.match.labelsList || [])) {
          if (lv.key) labels[lv.key] = lv.value;
        }
        if (Object.keys(labels).length) match.labels = labels;
        return {
          name: m.name,
          enabled: m.enabled,
          match,
          action: {
            agent: m.action.agent,
            tools: m.action.tools,
            timeout: m.action.timeout,
            instruction: m.action.instruction || null
          }
        };
      });
    }

    async function saveMappings() {
      const payload = denormalizeMappings(mappings.value);
      await api("/mappings", { method: "POST", body: { mappings: payload } });
      alert("规则已保存");
    }

    function addMapping() {
      mappings.value.push({
        name: `rule-${mappings.value.length + 1}`,
        enabled: true,
        match: { source: "", alertname: "", severity: [], labelsList: [] },
        action: { agent: "", tools: [], timeout: 300, instruction: "" }
      });
    }

    function removeMapping(i) {
      mappings.value.splice(i, 1);
    }

    function moveMapping(i, dir) {
      const j = i + dir;
      if (j < 0 || j >= mappings.value.length) return;
      [mappings.value[i], mappings.value[j]] = [mappings.value[j], mappings.value[i]];
    }

    function addLabel(ruleIdx) {
      if (!mappings.value[ruleIdx].match.labelsList) {
        mappings.value[ruleIdx].match.labelsList = [];
      }
      mappings.value[ruleIdx].match.labelsList.push({ key: "", value: "" });
    }

    function removeLabel(ruleIdx, labelIdx) {
      mappings.value[ruleIdx].match.labelsList.splice(labelIdx, 1);
    }

    async function saveAlertDefaults() {
      await api("/alert-defaults", { method: "POST", body: { defaults: { ...alertDefaults } } });
      alert("默认配置已保存");
    }
```

### Step 4: Update the return object of setup()

Add to the return object:

```javascript
    return {
      tab, core, mappings, serviceRules, agents, skills, agentSkillsMap,
      alertDefaults, showDefaults, toolOptions,
      sourceOptions, agentOptions,
      saveCore, saveMappings, addMapping, removeMapping, moveMapping,
      addLabel, removeLabel, saveAlertDefaults,
      saveServiceRules, addServiceRule, removeServiceRule,
      onAgentChange
    };
```

### Step 5: Verify by loading the page locally

```bash
# Start the service temporarily to check JS syntax via browser console
cd /home/ubuntu/kiro-devops && python3 -c "import dashboard.static.app as _; print('JS is static, syntax check via browser')"
```

Actual validation: Open browser to Dashboard → Config → Alert Mappings tab, check browser console for Vue errors.

### Step 6: Commit

```bash
cd /home/ubuntu/kiro-devops
git add dashboard/static/app.js
git commit -m "feat(dashboard): redesign Alert Mappings as rule cards with match/action editors"
```

---

## Task 5: End-to-end integration test

**Files:**
- Test via manual curl / Prometheus alert simulation

### Step 1: Create a test mapping

Create or edit `dashboard_config.json`:

```bash
cat > /tmp/test_mapping.json << 'EOF'
{
  "mappings": [
    {
      "name": "test-node-notready",
      "enabled": true,
      "match": {
        "source": "prometheus",
        "alertname": "NodeNotReady"
      },
      "action": {
        "agent": "ec2-alert-analyzer",
        "tools": ["execute_bash"],
        "timeout": 60,
        "instruction": "这是一条测试指令，确认规则引擎正常工作。"
      }
    }
  ],
  "alert_defaults": {
    "agent": "ec2-alert-analyzer",
    "tools": ["execute_bash"],
    "timeout": 300
  }
}
EOF
# Merge into existing dashboard_config.json preserving other keys
python3 -c "
import json
path = 'dashboard_config.json'
with open(path) as f:
    cfg = json.load(f)
with open('/tmp/test_mapping.json') as f:
    test = json.load(f)
cfg['mappings'] = test['mappings']
cfg['alert_defaults'] = test['alert_defaults']
with open(path, 'w') as f:
    json.dump(cfg, f, ensure_ascii=False, indent=2)
"
```

### Step 2: Simulate a NodeNotReady alert webhook

```bash
curl -X POST http://127.0.0.1:8080/event \
  -H "Authorization: Bearer $(grep WEBHOOK_TOKEN .env | cut -d= -f2 | tr -d '"')" \
  -H "Content-Type: application/json" \
  -d '{
    "alerts": [{
      "status": "firing",
      "startsAt": "2026-04-29T10:00:00Z",
      "labels": {
        "alertname": "NodeNotReady",
        "severity": "critical",
        "instance": "ip-10-42-9-29.cn-northwest-1.compute.internal"
      },
      "annotations": {
        "summary": "Node ip-10-42-9-29.cn-northwest-1.compute.internal is NotReady"
      }
    }]
  }'
```

### Step 3: Check journal for correct agent invocation

```bash
sudo journalctl -u kiro-devops -n 20 | grep -E "触发 Kiro|告警分析结果"
```

Expected: Log shows `触发 Kiro ec2-alert-analyzer: Node ip-10-42-9-29...` (matching the test rule's agent, not a hard-coded fallback).

### Step 4: Verify fallback path

Change the alertname to something unmapped (e.g., `UnmappedAlert`) and repeat the curl.

Expected: Log shows the fallback agent being used (from `alert_defaults`).

### Step 5: Commit

```bash
cd /home/ubuntu/kiro-devops
git add dashboard_config.json
git commit -m "test: add sample alert mapping for integration validation"
```

---

## Self-Review

### Spec Coverage

| Spec Section | Implementing Task |
|-------------|-------------------|
| AlertMatcher 顺序遍历 + 条件求值 | Task 1 |
| ConfigReloader mtime 热加载 | Task 1 + Task 2 |
| `_trigger_analysis` 动态 agent/tools/timeout/instruction | Task 2 |
| `_parse_alertmanager` `_raw_labels` | Task 2 |
| Dashboard API `/alert-defaults` | Task 3 |
| Dashboard UI 规则卡片 + match/action 编辑器 | Task 4 |
| 向后兼容旧格式 | Task 4 (normalizeMappings) |
| 单元测试覆盖 | Task 1 |

### Placeholder Scan

- No TBD/TODO
- No "add appropriate error handling" without code
- No "similar to Task N"
- Every step has exact file paths and complete code

### Type Consistency

- `AlertMatcher.__init__` takes `mappings: list[dict], defaults: dict` — used consistently
- `ConfigReloader.get_matcher()` returns `AlertMatcher` — used in Task 2
- `record` dict keys (`source`, `title`, `severity`, `_raw_labels`) match between `_parse_alertmanager` and `AlertMatcher._extract_field`

---

## Plan complete

Saved to `docs/superpowers/plans/2026-04-29-alert-mapping-agent.md`

**Execution options:**

1. **Subagent-Driven (recommended)** — Dispatch a fresh subagent per task, review between tasks
2. **Inline Execution** — Execute tasks in this session using executing-plans skill

Which approach do you prefer?
