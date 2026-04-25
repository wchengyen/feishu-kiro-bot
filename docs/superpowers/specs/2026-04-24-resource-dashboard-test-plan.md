# Resource Dashboard 测试方案

## 概述

本文档覆盖 Resource Dashboard 全链路测试：从后端 AWS/CloudWatch 数据链路，到前端渲染与交互。

---

## 一、单元测试（pytest）

### 1.1 资源发现模块 (`tests/test_dashboard_resources.py`)

| 用例 | 输入 | 期望输出 | 已有测试 |
|------|------|---------|---------|
| EC2 发现 | mock describe_instances 返回 1 台 running 实例 | Resource 列表，id=`ec2:i-123`，name=`test1` | ✅ |
| RDS 发现 | mock describe_db_instances 返回 1 个 available 实例 | Resource 列表，id=`rds:my-db` | ✅ |
| EC2 无 Name Tag | Tags 为空 | name = InstanceId | ✅ |
| CloudWatch 7 天数据 | mock 7 个 Datapoints | `[10.5, 20.0, ..., 22.7]` | ✅ |
| CloudWatch boto3 缺失 | `sys.modules` 移除 boto3 | `[]` | ✅ |
| 聚合接口 | mock discover + CW 返回数据 | resources[0].sparkline=7点, current=末值 | ✅ |
| 缓存命中 | 连续两次调用，第二次不触发 discover/CW | 返回缓存数据，mock 不再调用 | ✅ |

### 1.2 ConfigStore (`tests/test_dashboard_config_store.py`)

| 用例 | 已有测试 |
|------|---------|
| pinned_resources 读写 roundtrip | ✅ |
| 缺失文件返回 `[]` | ✅ |
| 不覆盖其他 keys（如 mappings） | ✅ |

### 1.3 API 路由 (`tests/test_dashboard_api_resources.py`)

| 用例 | 输入 | 期望 | 已有测试 |
|------|------|------|---------|
| GET /resources | mock 聚合返回 1 资源 | `ok=true`, resources 长度为 1 | ✅ |
| GET /resources?type=ec2 | mock 返回 EC2+RDS | 只返回 EC2 | ✅ |
| GET /resources/pins | 空配置 | `pins=[]` | ✅ |
| POST /resources/pins + GET 验证 | POST `["ec2:i-123"]` | GET 返回相同 pins | ✅ |

---

## 二、集成测试（真实 AWS 环境）

> ⚠️ 需要服务器已配置 AWS 凭证（Instance Profile 或 `~/.aws/credentials`）

### 2.1 后端直连测试

```bash
# 进入 worktree
cd /home/ubuntu/kiro-devops/.worktrees/resource-dashboard

# Python 交互式验证
python3 -c "
from dashboard.resources import discover_all, get_all_resources_with_metrics

# 测试 1：纯发现（不查指标，速度快）
resources = discover_all()
print(f'发现 {len(resources)} 个资源')
for r in resources[:5]:
    print(f'  {r.id} | {r.name} | {r.status}')

# 测试 2：完整聚合（含 CloudWatch，可能慢）
data = get_all_resources_with_metrics(refresh=True)
print(f'\\n聚合完成：{len(data[\"resources\"])} 个资源')
for r in data['resources'][:3]:
    spark = r.get('sparkline', [])
    print(f'  {r[\"id\"]} | CPU: {r.get(\"current\")}% | sparkline: {len(spark)} 个点')
"
```

**期望：**
- `discover_all()` 返回账号下 running/stopped 的 EC2 和 available 的 RDS
- `get_all_resources_with_metrics()` 返回相同资源，且 sparkline 为 7 个浮点数（新实例可能不足 7 个）

### 2.2 API 端到端测试

```bash
# 1. 登录获取 cookie
curl -c /tmp/dashboard_cookie.txt -X POST http://localhost:5000/api/dashboard/auth \
  -H "Content-Type: application/json" \
  -d '{"token": "你的DASHBOARD_TOKEN"}'

# 2. 获取资源列表
curl -b /tmp/dashboard_cookie.txt http://localhost:5000/api/dashboard/resources

# 3. 带过滤
curl -b /tmp/dashboard_cookie.txt "http://localhost:5000/api/dashboard/resources?type=ec2"

# 4. 强制刷新（绕过缓存）
curl -b /tmp/dashboard_cookie.txt "http://localhost:5000/api/dashboard/resources?refresh=1"

# 5. 设置 pinned
curl -b /tmp/dashboard_cookie.txt -X POST http://localhost:5000/api/dashboard/resources/pins \
  -H "Content-Type: application/json" \
  -d '{"pins": ["ec2:i-xxxxxxxxx"]}'

# 6. 读取 pinned
curl -b /tmp/dashboard_cookie.txt http://localhost:5000/api/dashboard/resources/pins
```

---

## 三、前端手动测试（浏览器）

### 3.1 页面加载

1. 打开 `http://<服务器IP>:8080/dashboard/#/resources`
2. **期望**：
   - Sidebar 有 "Resources" 入口
   - 页面标题显示 "Resources"
   - Toolbar 有刷新按钮、类型下拉、搜索框、仅看 Pinned checkbox
   - 表格列：⭐ | Name | Type | ID | Status | 7d Trend | CPU

### 3.2 数据渲染验证

| 检查项 | 期望 |
|--------|------|
| EC2 行 Type badge | 蓝色背景，文字 "ec2" |
| RDS 行 Type badge | 紫色背景，文字 "rds" |
| Sparkline | 100×30px 的 SVG 折线图，颜色与类型对应 |
| 当前 CPU | 如 `42%`，无数据时显示 `-` |
| 新实例（<7天） | sparkline 显示 `-` 或较少点数 |

### 3.3 Pin 交互验证

| 操作 | 期望 |
|------|------|
| 点击某行 ☆ | 变为 ★，该行置顶，左侧出现金色竖条 |
| 点击 ★ | 变为 ☆，取消置顶，金色竖条消失 |
| 勾选"仅看 Pinned" | 只显示 pinned 资源 |
| 取消勾选 | 显示全部资源 |
| 刷新页面 | pinned 状态保留（持久化到 dashboard_config.json） |

### 3.4 过滤与搜索验证

| 操作 | 期望 |
|------|------|
| Type 下拉选 EC2 | 只显示 EC2 资源 |
| Type 下拉选 RDS | 只显示 RDS 资源 |
| 搜索框输入 `test1` | 只显示 Name 或 ID 包含 test1 的资源 |
| 点击 🔃 刷新 | 数据重新加载（绕过 5 分钟缓存） |

---

## 四、性能测试

### 4.1 后端响应时间

```bash
# 测试缓存命中速度（第二次应 < 100ms）
time curl -b /tmp/dashboard_cookie.txt http://localhost:5000/api/dashboard/resources > /dev/null

# 测试强制刷新速度（取决于 AWS API，目标 < 3s）
time curl -b /tmp/dashboard_cookie.txt "http://localhost:5000/api/dashboard/resources?refresh=1" > /dev/null
```

### 4.2 大数据量场景

如果账号下 EC2/RDS 超过 50 个：
- 验证首次加载是否在 3 秒内
- 验证 sparkline 渲染是否卡顿（纯 SVG 应该很流畅）

---

## 五、边界情况测试

| 场景 | 测试方法 | 期望表现 |
|------|---------|---------|
| 服务器无 AWS 凭证 | 临时移走 `~/.aws/credentials` | API 返回 `resources: []`，前端显示提示条而非白屏 |
| CloudWatch 无数据 | 找一台刚启动 <1 天的实例 | sparkline 显示 `-`，current 显示 `-` |
| Prometheus 未配置 | 这是预留功能，MVP 不涉及 | 不测试 |
| 空账号（无 EC2/RDS） | 用全新 AWS 账号或把所有实例停掉 | 表格显示"暂无数据" |
| 并发 pin 修改 | 两个浏览器同时登录改 pin | 后保存的覆盖先保存的（当前架构限制，可接受） |

---

## 六、回归测试

确保新功能没有破坏现有 Dashboard：

```bash
cd /home/ubuntu/kiro-devops/.worktrees/resource-dashboard
python3 -m pytest tests/test_dashboard*.py -q
```

**期望：** 全部通过（当前 baseline 38 passed）。

需重点检查：
- `/agents` 页面正常加载
- `/events` 过滤、新建、删除正常
- `/scheduler` CRUD 正常
- `/config` 三 tab 读写正常
- 登录/登出正常
