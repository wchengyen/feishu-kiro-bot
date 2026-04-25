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
