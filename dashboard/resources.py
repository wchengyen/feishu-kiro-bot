from collections import defaultdict
from dataclasses import dataclass, field
import datetime
import json
import os
import time

from dashboard.providers.aws import AWSProvider


@dataclass
class Resource:
    id: str
    type: str
    name: str
    raw_id: str
    status: str
    meta: dict = field(default_factory=dict)
    tags: dict = field(default_factory=dict)
    sparkline: list = field(default_factory=list)
    current: float | None = None
    stats_7d: dict = field(default_factory=lambda: {"avg": None, "p95": None, "max": None})
    stats_30d: dict = field(default_factory=lambda: {"avg": None, "p95": None, "max": None})


def _load_regions() -> list[str]:
    """从 dashboard_config.json 读取 regions 配置."""
    config_path = os.path.join(os.path.dirname(__file__), "..", "dashboard_config.json")
    if os.path.exists(config_path):
        with open(config_path) as f:
            config = json.load(f)
            return config.get("regions", [])
    return []


def _new_resource_to_old(resource):
    """Translate new provider Resource to legacy Resource format."""
    return Resource(
        id=f"{resource.resource_type}:{resource.region}:{resource.id}",
        type=resource.resource_type,
        name=resource.name,
        raw_id=resource.id,
        status=resource.status,
        meta=resource.meta,
        tags=resource.tags,
    )


def discover_ec2(region: str | None = None):
    provider = AWSProvider()
    if region:
        resources = provider.discover_resources(region, "ec2")
    else:
        try:
            import boto3
        except ImportError:
            return []
        client = boto3.client("ec2")
        region_name = client._client_config.region_name or ""
        resources = provider.discover_resources(region_name, "ec2")
    return [_new_resource_to_old(r) for r in resources]


def discover_rds(region: str | None = None):
    provider = AWSProvider()
    if region:
        resources = provider.discover_resources(region, "rds")
    else:
        try:
            import boto3
        except ImportError:
            return []
        client = boto3.client("rds")
        region_name = client._client_config.region_name or ""
        resources = provider.discover_resources(region_name, "rds")
    return [_new_resource_to_old(r) for r in resources]


def discover_all():
    regions = _load_regions()
    if not regions:
        # 回退到默认行为：单个默认区域
        return discover_ec2() + discover_rds()
    all_resources = []
    for region in regions:
        try:
            all_resources.extend(discover_ec2(region))
        except Exception as e:
            # 记录错误但继续其他区域
            import logging
            logging.getLogger("dashboard.resources").warning(f"EC2 discovery failed in {region}: {e}")
        try:
            all_resources.extend(discover_rds(region))
        except Exception as e:
            import logging
            logging.getLogger("dashboard.resources").warning(f"RDS discovery failed in {region}: {e}")
    return all_resources


def get_cloudwatch_metrics(resource_id, namespace, dimension_name, days=7, region=None):
    provider = AWSProvider()
    points = provider._fetch_cloudwatch_datapoints(resource_id, namespace, dimension_name, days, region)
    return [
        {"Timestamp": p["Timestamp"], "Average": p["Average"], "Maximum": p["Maximum"]}
        for p in points
    ]


def compute_stats(points):
    if not points:
        return {"avg": None, "p95": None, "max": None}
    averages = [p["Average"] for p in points]
    maxima = [p["Maximum"] for p in points]
    sorted_avgs = sorted(averages)
    idx = int(len(sorted_avgs) * 0.95)
    p95_val = sorted_avgs[min(idx, len(sorted_avgs) - 1)]
    return {
        "avg": round(sum(averages) / len(averages), 1),
        "p95": round(p95_val, 1),
        "max": round(max(maxima), 1),
    }


def sparkline_from_points(points):
    if not points:
        return []
    daily = defaultdict(list)
    for p in points:
        day = p["Timestamp"].strftime("%Y-%m-%d")
        daily[day].append(p["Average"])
    return [round(sum(v) / len(v), 1) for v in daily.values()]


_cache = {"data": None, "ts": 0}
CACHE_TTL = 300


def get_all_resources_with_metrics(refresh=False):
    global _cache
    if (
        not refresh
        and _cache["data"] is not None
        and (time.time() - _cache["ts"]) < CACHE_TTL
    ):
        return _cache["data"]

    resources = discover_all()
    for r in resources:
        region = r.meta.get("region")
        if r.type == "ec2":
            metrics_7d = get_cloudwatch_metrics(r.raw_id, "AWS/EC2", "InstanceId", days=7, region=region)
            metrics_30d = get_cloudwatch_metrics(r.raw_id, "AWS/EC2", "InstanceId", days=30, region=region)
        elif r.type == "rds":
            metrics_7d = get_cloudwatch_metrics(
                r.raw_id, "AWS/RDS", "DBInstanceIdentifier", days=7, region=region
            )
            metrics_30d = get_cloudwatch_metrics(
                r.raw_id, "AWS/RDS", "DBInstanceIdentifier", days=30, region=region
            )
        else:
            metrics_7d = []
            metrics_30d = []

        r.sparkline = sparkline_from_points(metrics_7d)
        r.current = r.sparkline[-1] if r.sparkline else None
        r.stats_7d = compute_stats(metrics_7d)
        r.stats_30d = compute_stats(metrics_30d)

    data = {
        "resources": [resource_to_dict(r) for r in resources],
        "regions": _load_regions(),
        "cached": False,
        "error": None,
    }
    _cache = {"data": data, "ts": time.time()}
    return data


def resource_to_dict(r: Resource) -> dict:
    return {
        "id": r.id,
        "type": r.type,
        "name": r.name,
        "raw_id": r.raw_id,
        "status": r.status,
        "meta": r.meta,
        "tags": r.tags,
        "sparkline": r.sparkline,
        "current": r.current,
        "stats_7d": r.stats_7d,
        "stats_30d": r.stats_30d,
    }
