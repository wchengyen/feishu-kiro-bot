# Tencent Dashboard Resources Design

**Date:** 2026-04-27  
**Scope:** Add Tencent Cloud (CVM / Lighthouse) resource monitoring to the dashboard, fully aligned with existing AWS capabilities.  
**Approach:** Unified Provider abstraction layer (refactor AWS into `BaseResourceProvider`, add Tencent implementation).

---

## 1. Background & Goals

The dashboard currently supports AWS EC2/RDS discovery, CloudWatch CPU metrics, sparkline trends, and historical data persistence. We need to add Tencent Cloud support with:

- **Resource types:** CVM (Cloud Virtual Machine) and Lighthouse
- **Region:** `ap-tokyo` (configurable via `dashboard_config.json`)
- **Authentication:** Reuse existing `tccli` environment configuration (subprocess calls)
- **Metrics:** Full parity with AWS — 7-day / 30-day sparklines, `/history` endpoint, SQLite persistence
- **UI:** Independent route (`/#/resources/tencent`), toggleable via `setup.sh`

---

## 2. Architecture Overview

### Directory Structure

```
dashboard/
├── __init__.py
├── api.py                         # Unified routes, dispatch by provider
├── config_store.py                # Read/write dashboard_config.json (with migration)
├── metrics_store.py               # SQLite persistence, extended with provider column
├── providers/
│   ├── __init__.py                # Provider registry, get_provider(), get_all_enabled_providers()
│   ├── base.py                    # BaseResourceProvider ABC + Resource/Metric dataclasses
│   ├── aws.py                     # AWSProvider (migrated from resources.py)
│   └── tencent.py                 # TencentProvider (new)
├── resources.py                   # Compatibility shim, delegates to providers
├── kiro_scanner.py
└── static/
    └── app.js                     # Generic ResourcesPage with provider prop

scripts/
└── sync_resource_metrics.py       # Iterate enabled providers, sync metrics to store

tests/
├── test_providers_aws.py          # AWS provider unit tests (extracted from existing)
├── test_providers_tencent.py      # Tencent provider unit tests (mock subprocess)
├── test_dashboard_api_resources.py        # Updated to use provider-aware mocks
├── test_dashboard_api_resources_tencent.py # New: Tencent API route tests
└── test_config_store.py           # Config migration tests
```

---

## 3. Provider Abstract Interface

### `dashboard/providers/base.py`

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from datetime import datetime


@dataclass
class Resource:
    provider: str                      # "aws" | "tencent"
    type: str                          # "ec2" | "rds" | "cvm" | "lighthouse"
    region: str
    id: str                            # Raw cloud resource ID
    name: str
    status: str
    class_type: Optional[str] = None   # instance_type / db_instance_class / bundle_type
    os_or_engine: Optional[str] = None # platform / engine / os_name
    tags: Dict[str, str] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def unique_id(self) -> str:
        """Globally unique ID used in API, pins, SQLite, and frontend."""
        return f"{self.provider}:{self.type}:{self.region}:{self.id}"


@dataclass
class MetricPoint:
    timestamp: datetime
    value: float


@dataclass
class ResourceMetrics:
    resource_id: str
    metric_name: str                   # Unified internal name, e.g. "cpu_utilization"
    points_7d: List[MetricPoint]
    points_30d: List[MetricPoint]
    current: Optional[float] = None
    stats_7d: Optional[Dict] = None
    stats_30d: Optional[Dict] = None
    sparkline_7d: List[float] = field(default_factory=list)


class BaseResourceProvider(ABC):
    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def is_enabled(self) -> bool: ...

    @abstractmethod
    def regions(self) -> List[str]: ...

    @abstractmethod
    def resource_types(self) -> List[str]: ...

    @abstractmethod
    def discover_resources(
        self, region: str, resource_type: Optional[str] = None
    ) -> List[Resource]: ...

    @abstractmethod
    def get_metrics(
        self, resource: Resource, range_days: int = 7
    ) -> ResourceMetrics: ...

    @abstractmethod
    def sync_metrics_to_store(self, store, backfill_days: int = 1) -> None:
        """Background script entrypoint: discover all resources and persist metrics."""
        ...
```

### ID Convention

- **AWS (new):** `aws:ec2:cn-north-1:i-0abc123`
- **AWS (legacy migration):** Existing IDs like `ec2:cn-north-1:i-0abc123` are migrated to include the `aws:` prefix.
- **Tencent:** `tencent:cvm:ap-tokyo:ins-12345678`, `tencent:lighthouse:ap-tokyo:lhins-xyz`

---

## 4. AWS Provider Refactor

### Migration Mapping

| Original (`dashboard/resources.py`) | New (`dashboard/providers/aws.py`) |
|--------------------------------------|------------------------------------|
| `Resource` dataclass                 | `base.Resource` with `provider="aws"` |
| `discover_ec2(region)`               | `AWSProvider.discover_resources(region, "ec2")` |
| `discover_rds(region)`               | `AWSProvider.discover_resources(region, "rds")` |
| `get_cloudwatch_metrics(...)`        | `AWSProvider.get_metrics(resource, 7\|30)` |
| `_load_regions()`                    | `AWSProvider.regions()` |
| `_cache` (5-min TTL)                 | Moved to API layer (`dashboard/api.py`) |

### Compatibility Shim

`dashboard/resources.py` is retained as a thin compatibility layer during the transition:

```python
from dashboard.providers import get_provider

def get_all_resources_with_metrics(refresh=False, ...):
    provider = get_provider("aws")
    # Preserve existing signature and return shape
    ...
```

### Behavior Guarantee

- All existing boto3 calls, filters (`running`/`stopped`), tag fetching, and CloudWatch metric parameters remain **identical**.
- Existing tests are updated to mock `AWSProvider` instead of `dashboard.resources` internals.

---

## 5. Tencent Provider Implementation

### `dashboard/providers/tencent.py`

#### Subprocess Wrapper

```python
import json
import subprocess
from typing import Any, Dict


def _tccli(service: str, action: str, region: str, payload: Dict[str, Any] = None) -> Dict:
    cmd = ["tccli", service, action, "--region", region, "--output", "json"]
    if payload:
        cmd.extend(["--cli-input-json", json.dumps(payload)])
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(result.stdout)
```

#### Resource Discovery

| Type | tccli Command | Field Mapping |
|------|---------------|---------------|
| **CVM** | `cvm DescribeInstances` | `InstanceId` -> `id`, `InstanceName` -> `name`, `InstanceState` (`RUNNING`/`STOPPED`) -> `status`, `InstanceType` -> `class_type`, `OsName` -> `os_or_engine`, `Tags` -> `tags` |
| **Lighthouse** | `lighthouse DescribeInstances` | `InstanceId` -> `id`, `InstanceName` -> `name`, `InstanceState` -> `status`, `BundleId` -> `class_type`, `OsName` -> `os_or_engine` |

#### Metrics (Cloud Monitor)

```python
# tccli monitor GetMonitorData
payload = {
    "Namespace": "QCE/CVM",          # "QCE/LIGHTHOUSE" for Lighthouse
    "MetricName": "CPUUsage",        # Tencent Cloud CPU utilization
    "Instances": [{
        "Dimensions": [{"Name": "InstanceId", "Value": resource.id}]
    }],
    "Period": 3600,                  # 1-hour granularity
    "StartTime": start.isoformat(),
    "EndTime": end.isoformat()
}
data = _tccli("monitor", "GetMonitorData", resource.region, payload)
```

- **Internal metric name:** `cpu_utilization` (mapped from Tencent `CPUUsage`).
- **Period:** 3600 seconds (hourly), matching AWS CloudWatch granularity for 7d/30d ranges.
- **Data conversion:** `data["Timestamps"]` and `data["Values"]` are zipped into `List[MetricPoint]`.

#### Region Configuration

Read from `dashboard_config.json` -> `providers.tencent.regions`. Default during setup: `["ap-tokyo"]`.

---

## 6. Unified API Routes & Caching

### Route Changes (`dashboard/api.py`)

| Route | Change |
|-------|--------|
| `GET /api/dashboard/resources` | Add optional `?provider=aws\|tencent` (default `aws` for backward compatibility). Returns `resources`, `regions`, `pinned`, `cached` for the requested provider. |
| `POST /api/dashboard/resources/pins` | **Unchanged**. Pin IDs already carry the `provider:` prefix, so they are naturally cross-provider isolated. |
| `GET /api/dashboard/resources/<id>/history` | **Self-parse provider** from ID. `aws:ec2:...` -> `AWSProvider`; `tencent:cvm:...` -> `TencentProvider`. No additional query param needed. |

```python
def _parse_provider_from_id(resource_id: str) -> str:
    return resource_id.split(":", 1)[0]
```

### Caching

The global `_cache` dict is restructured to a two-level key:

```python
_cache: Dict[str, Any] = {}  # key: "{provider}:{region or 'all'}"

def _cache_key(provider: str, region: Optional[str] = None) -> str:
    return f"{provider}:{region or 'all'}"
```

This keeps AWS and Tencent caches independent and allows per-provider refresh.

---

## 7. Metrics Store Extension

### Schema Change

Add a `provider` column to both raw and aggregated tables.

```sql
CREATE TABLE raw_metrics_v2 (
    provider TEXT,              -- "aws" | "tencent"
    timestamp INTEGER,
    resource_id TEXT,
    metric TEXT,
    value REAL,
    PRIMARY KEY (provider, timestamp, resource_id, metric)
);
```

Same change applied to `aggregated_metrics.db`.

### Migration Strategy

On `MetricsStore` initialization:
1. Detect if `provider` column is missing (old schema).
2. Execute `ALTER TABLE raw_metrics ADD COLUMN provider TEXT DEFAULT 'aws'`.
3. Execute same on aggregated table.
4. All new writes include explicit `provider`.

### Query Adaptation

`MetricsStore.query_history(resource_id, metric, range)` extracts `provider` from `resource_id` and adds `WHERE provider = ?` to all queries.

### Metric Name Mapping

| Cloud Raw Name | Internal SQLite `metric` Column |
|----------------|---------------------------------|
| AWS `CPUUtilization` | `cpu_utilization` |
| Tencent `CPUUsage` | `cpu_utilization` |

Frontend requests `/history?metric=cpu_utilization` work identically for both providers.

---

## 8. Frontend Changes (`dashboard/static/app.js`)

### Router

```javascript
const routes = [
  { path: '/resources', redirect: '/resources/aws' },
  { path: '/resources/:provider(aws|tencent)', component: ResourcesPage, props: true },
  // ... existing routes
];
```

`ResourcesPage` receives `provider` as a prop. All internal data fetches append `?provider=${this.provider}`.

### Dynamic Adaptation

| Dynamic Item | AWS (`provider="aws"`) | Tencent (`provider="tencent"`) |
|--------------|------------------------|--------------------------------|
| Type filter options | `ec2`, `rds` | `cvm`, `lighthouse` |
| Class column label | `Instance Type` / `DB Class` | `Instance Type` / `Bundle` |
| OS/Engine column | `Platform` / `Engine` | `OS Name` |
| Region dropdown | `cn-north-1`, `cn-northwest-1` | `ap-tokyo` (from config) |

### Component Reuse

- **Sparkline SVG**, **History Chart**, **Pin toggle**, **Search/Filter logic** are fully reused.
- Only column definitions and type-filter options are provider-specific.

### Navigation Sidebar

Dynamically rendered based on enabled providers from `/api/dashboard/config`:

```javascript
navItems: [
  { label: 'AWS Resources', to: '/resources/aws', visible: config.providers.aws?.enabled },
  { label: 'Tencent Resources', to: '/resources/tencent', visible: config.providers.tencent?.enabled },
]
```

---

## 9. Configuration, Toggle Switch & `setup.sh`

### New `dashboard_config.json` Structure

```json
{
  "providers": {
    "aws": {
      "enabled": true,
      "regions": ["cn-north-1", "cn-northwest-1"]
    },
    "tencent": {
      "enabled": true,
      "regions": ["ap-tokyo"]
    }
  },
  "pins": ["aws:ec2:cn-north-1:i-0abc123"],
  "alert_mappings": {},
  "service_rules": {}
}
```

### Backward Compatibility

`config_store.py` read path:
- If top-level `regions` exists but `providers` does not, auto-wrap into `{"providers": {"aws": {"enabled": true, "regions": old_regions}}}`.
- Write-back always uses the new format.

### `setup.sh` Changes

At the end of existing setup flow, add:

```bash
read -p "Enable Tencent Cloud dashboard? [y/N] " enable_tencent
if [[ "$enable_tencent" =~ ^[Yy]$ ]]; then
    read -p "Tencent regions to monitor [ap-tokyo]: " tencent_regions
    tencent_regions=${tencent_regions:-ap-tokyo}
    # Write to dashboard_config.json via Python helper
fi
```

- If enabled, warn if `tccli` is not found in `$PATH`.
- If disabled, `tencent.enabled` is set to `false` and the sidebar/API routes gracefully hide Tencent.

---

## 10. Background Sync Script

`scripts/sync_resource_metrics.py` is refactored from AWS-only to provider-agnostic:

```python
from dashboard.providers import get_all_enabled_providers
from dashboard.metrics_store import MetricsStore

store = MetricsStore()

for provider in get_all_enabled_providers():
    print(f"Syncing metrics for {provider.name} ...")
    provider.sync_metrics_to_store(store, backfill_days=args.backfill_days)

store.close()
```

`BaseResourceProvider.sync_metrics_to_store` default implementation:
1. Iterate `self.regions()`.
2. Discover resources per region.
3. Fetch metrics per resource (or in provider-optimized batches).
4. Persist to `MetricsStore`.

AWSProvider preserves existing CloudWatch batching logic. TencentProvider uses hourly `tccli monitor` calls; acceptable given expected `ap-tokyo` resource count.

---

## 11. Testing Strategy

| Test File | Purpose |
|-----------|---------|
| `tests/test_providers_aws.py` | Unit-test `AWSProvider.discover_resources` and `get_metrics` with mocked boto3. Extracted/refactored from existing API tests. |
| `tests/test_providers_tencent.py` | Unit-test `TencentProvider` with mocked `subprocess.run`. Uses JSON fixture files (`tencent_cvm_response.json`, `tencent_lighthouse_response.json`, `tencent_monitor_response.json`). |
| `tests/test_dashboard_api_resources.py` | Updated mocks target `AWSProvider`. Assertions remain unchanged to verify backward compatibility. |
| `tests/test_dashboard_api_resources_tencent.py` | New. Mock `TencentProvider`, test `GET /api/dashboard/resources?provider=tencent` response shape, filtering, and `GET /history` for Tencent IDs. |
| `tests/test_config_store.py` | Verify old-format `regions: [...]` auto-migrates to new `providers.aws.regions`. Verify Tencent config read/write. |

### Mock Data Fixtures

- `tests/fixtures/tencent_cvm_describe.json` -- sample `tccli cvm DescribeInstances` output
- `tests/fixtures/tencent_lighthouse_describe.json` -- sample `tccli lighthouse DescribeInstances` output
- `tests/fixtures/tencent_monitor_cpu.json` -- sample `tccli monitor GetMonitorData` output

---

## 12. Data Migration & Rollout

1. **Config migration:** Automatic on first read by `config_store.py`. No manual action.
2. **SQLite migration:** Automatic on first `MetricsStore` initialization. `ALTER TABLE ADD COLUMN provider DEFAULT 'aws'`.
3. **Pin migration:** Existing pins like `ec2:cn-north-1:i-xxx` are rewritten to `aws:ec2:cn-north-1:i-xxx` on config load.
4. **Zero-downtime:** Existing AWS dashboard continues to work throughout the refactor. Tencent routes only appear after `setup.sh` enables them.

---

## 13. Open Questions / Notes

- **Lighthouse metrics namespace:** Verified as `QCE/LIGHTHOUSE` in Tencent Cloud Monitor documentation; if `tccli` returns different namespace strings, adjust `tencent.py` accordingly.
- **tccli output stability:** Rely on `--output json` for machine parsing. If Tencent changes field names in future `tccli` versions, provider tests will catch regressions.
- **Performance:** `tccli` subprocess overhead is acceptable for hourly sync of ~dozens of instances. If scale grows beyond 100 instances, consider batching or switching to `tencentcloud-sdk-python`.
