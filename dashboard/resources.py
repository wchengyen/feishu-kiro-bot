from dataclasses import dataclass, field


@dataclass
class Resource:
    id: str
    type: str
    name: str
    raw_id: str
    status: str
    meta: dict = field(default_factory=dict)
    sparkline: list = field(default_factory=list)
    current: float | None = None


def discover_ec2():
    try:
        import boto3
    except ImportError:
        return []
    client = boto3.client("ec2")
    resp = client.describe_instances(
        Filters=[{"Name": "instance-state-name", "Values": ["running", "stopped"]}]
    )
    resources = []
    for reservation in resp.get("Reservations", []):
        for inst in reservation.get("Instances", []):
            name = ""
            for tag in inst.get("Tags", []):
                if tag.get("Key") == "Name":
                    name = tag.get("Value", "")
                    break
            resources.append(
                Resource(
                    id=f"ec2:{inst['InstanceId']}",
                    type="ec2",
                    name=name or inst["InstanceId"],
                    raw_id=inst["InstanceId"],
                    status=inst["State"]["Name"],
                    meta={"instance_type": inst.get("InstanceType", "")},
                )
            )
    return resources


def discover_rds():
    try:
        import boto3
    except ImportError:
        return []
    client = boto3.client("rds")
    resp = client.describe_db_instances()
    resources = []
    for db in resp.get("DBInstances", []):
        resources.append(
            Resource(
                id=f"rds:{db['DBInstanceIdentifier']}",
                type="rds",
                name=db["DBInstanceIdentifier"],
                raw_id=db["DBInstanceIdentifier"],
                status=db["DBInstanceStatus"],
                meta={"engine": db.get("Engine", "")},
            )
        )
    return resources


def discover_all():
    return discover_ec2() + discover_rds()


import datetime


def get_cloudwatch_cpu(resource_id, namespace, dimension_name, days=7):
    try:
        import boto3
    except ImportError:
        return []
    client = boto3.client("cloudwatch")
    end = datetime.datetime.utcnow()
    start = end - datetime.timedelta(days=days)
    resp = client.get_metric_statistics(
        Namespace=namespace,
        MetricName="CPUUtilization",
        Dimensions=[{"Name": dimension_name, "Value": resource_id}],
        StartTime=start,
        EndTime=end,
        Period=86400,
        Statistics=["Average"],
    )
    points = sorted(resp.get("Datapoints", []), key=lambda x: x["Timestamp"])
    return [round(p["Average"], 1) for p in points]

import time

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
        if r.type == "ec2":
            r.sparkline = get_cloudwatch_cpu(r.raw_id, "AWS/EC2", "InstanceId")
        elif r.type == "rds":
            r.sparkline = get_cloudwatch_cpu(
                r.raw_id, "AWS/RDS", "DBInstanceIdentifier"
            )
        if r.sparkline:
            r.current = r.sparkline[-1]

    data = {
        "resources": [resource_to_dict(r) for r in resources],
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
        "sparkline": r.sparkline,
        "current": r.current,
    }
