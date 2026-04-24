# Resource Dashboard 设计文档

## 背景与目标

在现有 Kiro Dashboard 中新增 **Resources** 页面，自动发现 AWS 账号下的 EC2/RDS/EKS 资源，并展示过去 7 天的 CPU 利用率趋势（sparkline 迷你图）。

**目标用户**：运维/开发人员，需要快速一览核心云资源健康状态。

**成功标准**：
- 打开 Resources 页面 3 秒内看到资源列表 + sparkline
- 用户可 pin 关注的核心资源，置顶高亮展示
- 零重型图表库依赖，保持现有零构建 Vue 3 技术栈

---

## 设计决策摘要

| 决策项 | 选择 | 理由 |
|--------|------|------|
| AWS 认证 | 服务器默认凭证链（boto3） | 无需在 UI 管理 AK/SK，安全且简单 |
| 指标来源 | EC2/RDS → CloudWatch；EKS → Prometheus | 每种资源用最自然的数据源 |
| 展示形式 | 列表 + SVG sparkline | 零依赖、符合现有技术栈、信息密度高 |
| 资源发现 | 自动发现 + UI pin/收藏 | 开箱即用，同时避免信息过载 |
| 缓存策略 | 后端内存缓存，TTL 5 分钟 | 减少 AWS API 调用次数和延迟 |
| 实现顺序 | MVP 先做 EC2/RDS + CloudWatch | 路径最顺，EKS+Prometheus 第二阶段补 |

---

## 架构与数据流

```
┌─────────────┐      GET /resources              ┌──────────────┐
│   Browser   │ ───────────────────────────────> │  Flask API   │
│  (Vue 3)    │                                  │  (api.py)    │
└─────────────┘                                  └──────┬───────┘
                                                        │
                              ┌─────────────────────────┼─────────────────────────┐
                              ↓                         ↓                         ↓
                       ┌────────────┐           ┌────────────┐           ┌────────────┐
                       │  Memory    │           │  Discover  │           │   Query    │
                       │  Cache     │           │  (boto3)   │           │  Metrics   │
                       │  TTL 300s  │           │            │           │            │
                       └────────────┘           └─────┬──────┘           └─────┬──────┘
                                                       │                        │
                              ┌────────────────────────┼────────────────────────┤
                              ↓                        ↓                        ↓
                       ┌────────────┐          ┌────────────┐          ┌────────────┐
                       │  EC2 API   │          │  RDS API   │          │ CloudWatch │
                       │            │          │            │          │ Prometheus │
                       └────────────┘          └────────────┘          └────────────┘
```

**数据流说明**：
1. 前端访问 `/resources` → GET `/api/dashboard/resources?type=`
2. `dashboard/resources.py` 检查内存缓存，命中直接返回
3. 未命中：并行 boto3 发现 EC2/RDS → 对每个资源查 CloudWatch（EC2/RDS）或 Prometheus（EKS）→ 取过去 7 天每天 1 个平均值
4. 返回结构化 JSON（资源数组，每项含 7 个数据点）
5. 前端用纯 SVG `<polyline>` 绘制 sparkline

---

## 后端模块设计

### 新增文件：`dashboard/resources.py`

```python
def discover_all() -> list[Resource]:
    """并行调用 boto3 发现 EC2/RDS/EKS，返回统一 Resource 对象列表。"""

def get_cloudwatch_cpu(resource_id: str, namespace: str, days=7) -> list[float | None]:
    """查 CloudWatch GetMetricStatistics，每天 1 个 Average，返回 7 个浮点数（或 null）。"""

def get_prometheus_cpu(cluster_name: str, days=7) -> list[float | None]:
    """调 Prometheus /api/v1/query_range，返回 7 个浮点数（或 null）。"""

def get_all_resources_with_metrics() -> dict:
    """组装 discover + metrics，写入内存缓存，返回大 JSON。"""
```

**Resource 统一对象结构**：
```json
{
  "id": "ec2:i-0abcd1234",
  "type": "ec2",
  "name": "test1",
  "raw_id": "i-0abcd1234",
  "status": "running",
  "meta": { "instance_type": "t3.medium" },
  "sparkline": [12.5, 23.0, 45.2, 38.1, 29.0, 51.2, 42.0],
  "current": 42.0
}
```

### 缓存策略

- 模块级全局变量 `_cache = {"data": <dict>, "ts": <float>}`
- TTL = 300 秒
- 前端 toolbar 提供"刷新"按钮，通过 query param `?refresh=1` 强制绕过缓存
- AWS API 调用在 discover 阶段用 `concurrent.futures.ThreadPoolExecutor` 并行

---

## API 规范

### GET `/api/dashboard/resources`

**Query Parameters：**
- `type`（可选）：`ec2` | `rds` | `eks`，不传则返回全部
- `refresh`（可选）：`1` 表示强制绕过缓存

**Response 200：**
```json
{
  "ok": true,
  "resources": [
    {
      "id": "ec2:i-0abcd1234",
      "type": "ec2",
      "name": "test1",
      "raw_id": "i-0abcd1234",
      "status": "running",
      "meta": { "instance_type": "t3.medium" },
      "sparkline": [12.5, 23.0, 45.2, 38.1, 29.0, 51.2, 42.0],
      "current": 42.0
    }
  ],
  "pinned": ["ec2:i-0abcd1234"],
  "cached": false,
  "error": null
}
```

### GET `/api/dashboard/resources/pins`

**Response 200：**
```json
{ "ok": true, "pins": ["ec2:i-0abcd1234", "rds:my-db"] }
```

### POST `/api/dashboard/resources/pins`

**Request Body：**
```json
{ "pins": ["ec2:i-0abcd1234", "rds:my-db"] }
```

**Response 200：**
```json
{ "ok": true }
```

---

## 前端设计

### 路由与导航

- `app.js` 新增 `ResourcesPage` 组件，路由 `/resources`
- Sidebar 导航新增 "Resources" 入口

### 表格列设计

| 列 | 内容 |
|----|------|
| ⭐ Pin | 星形图标按钮，toggle pin 状态 |
| Name | 资源名称（EC2 Name Tag / RDS DBInstanceIdentifier / EKS cluster name） |
| Type | EC2 / RDS / EKS badge |
| ID | 实例 ID 或集群名 |
| Status | running / available / ACTIVE 等 |
| Sparkline | 纯 SVG，100×30px，7 个点连成的折线 |
| 当前值 | 最新一天 CPU 百分比，如 `42%` |

**Sparkline 实现（零依赖）**：

后端返回 `sparkline: [12.5, 23.0, ...]`，前端用纯函数生成 SVG：

```javascript
function sparklineSvg(points, color) {
  const min = Math.min(...points.filter(v => v != null));
  const max = Math.max(...points.filter(v => v != null));
  const range = max - min || 1;
  const pts = points.map((v, i) => {
    if (v == null) return "";
    const x = (i / (points.length - 1)) * 100;
    const y = 30 - ((v - min) / range) * 30;
    return `${x},${y}`;
  }).filter(Boolean).join(" ");
  return `<svg viewBox="0 0 100 30" width="100" height="30"><polyline fill="none" stroke="${color}" stroke-width="2" points="${pts}"/></svg>`;
}
```

颜色按类型区分：EC2 蓝 `#3b82f6`、RDS 紫 `#8b5cf6`、EKS 橙 `#f59e0b`。

### Pin 交互

- Pinned 资源排在最前面，背景色 `#f8fafc`，左侧加 3px 金色竖条
- Unpinned 资源正常白色背景，排在后面
- 点击 ⭐ 立即调 POST `/resources/pins`，同时本地 reorder 列表，无需刷新页面
- Toolbar 提供"仅看 Pinned"过滤开关

### Toolbar 元素

- 🔃 刷新按钮（强制跳过缓存）
- 类型筛选：全部 / EC2 / RDS / EKS
- 搜索框：按 Name 或 ID 过滤
- ⭐ 仅看 Pinned toggle

---

## 错误处理

### AWS 侧

- **无 AWS 凭证 / 权限不足**：catch `NoCredentialsError` / `ClientError`，返回 `{"resources": [], "error": "AWS 凭证未配置或缺少权限"}`，前端表格上方显示提示条
- **资源过多**：首次发现限定返回前 **50 个 running 状态**资源，stopped 的折叠在"显示更多"里
- **CloudWatch 无数据**：新启动实例可能不足 7 天数据，缺失值用 `null`，sparkline 留空，当前值显示 `-`

### Prometheus 侧

- **Prometheus 地址未配置**：EKS 区域直接不展示，或显示"未配置 Prometheus"
- **查询失败 / 超时**：catch `requests.ConnectionError`，该行 sparkline 显示 `-`，不阻断整体列表
- **EKS 集群无容器指标**：Prometheus 返回空矩阵，sparkline 为空

### 前端侧

- **大数据量渲染**：列表超过 100 行时后端分页，首次只返回 50 条，底部"加载更多"
- **Pin 状态冲突**：多人同时登录同一 Dashboard 改 pin 会覆盖。当前架构是单用户本地工具，接受此限制

---

## 实现范围

### MVP（本期必做）

1. `dashboard/resources.py`：EC2/RDS 发现 + CloudWatch CPU 查询
2. `dashboard/api.py`：`/resources` GET、`/resources/pins` GET/POST
3. `dashboard/config_store.py`：支持 `pinned_resources` 读写
4. `app.js`：`ResourcesPage` + sparkline SVG helper + pin 交互
5. `style.css`：资源表格 + sparkline + pin 高亮样式
6. `.env.example`：新增 `PROMETHEUS_URL`（可选配置，MVP 阶段不使用）

### 后续扩展（本期不做）

- EKS Prometheus 查询（架构预留，第二阶段补）
- CPU 以外的指标（内存、磁盘、网络）
- 资源详情页 / 大图表面板
- 自动告警阈值线

---

## 技术选型理由

- **boto3 默认凭证链**：项目已有 AWS 使用场景（ec2-alert-analyzer skill），服务器上已有凭证配置，无需新增 AK/SK 管理逻辑
- **纯 SVG sparkline**：不引入 Chart.js/ECharts 等重型库，保持零构建、零额外依赖，与现有 dashboard 哲学一致
- **内存缓存而非 Redis**：dashboard 是单机单用户工具，内存缓存足够简单且有效
- **dashboard_config.json 存 pin 状态**：复用现有 Config 持久化机制，无需新增数据库表
