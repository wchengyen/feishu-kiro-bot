#!/usr/bin/env python3
"""Tests for dashboard resources discovery and metrics."""

from unittest.mock import MagicMock, patch

import pytest

from dashboard.resources import Resource, discover_ec2, discover_rds, discover_all


@patch("boto3.client")
def test_discover_ec2_returns_instances(mock_client):
    mock_ec2 = MagicMock()
    mock_ec2.describe_instances.return_value = {
        "Reservations": [
            {
                "Instances": [
                    {
                        "InstanceId": "i-123",
                        "State": {"Name": "running"},
                        "InstanceType": "t3.micro",
                        "Tags": [{"Key": "Name", "Value": "test1"}],
                    }
                ]
            }
        ]
    }
    mock_client.return_value = mock_ec2

    result = discover_ec2()
    assert len(result) == 1
    assert result[0].id == "ec2:i-123"
    assert result[0].name == "test1"
    assert result[0].status == "running"
    assert result[0].meta["instance_type"] == "t3.micro"


@patch("boto3.client")
def test_discover_rds_returns_instances(mock_client):
    mock_rds = MagicMock()
    mock_rds.describe_db_instances.return_value = {
        "DBInstances": [
            {
                "DBInstanceIdentifier": "my-db",
                "DBInstanceStatus": "available",
                "Engine": "mysql",
            }
        ]
    }
    mock_client.return_value = mock_rds

    result = discover_rds()
    assert len(result) == 1
    assert result[0].id == "rds:my-db"
    assert result[0].status == "available"


@patch("boto3.client")
def test_discover_ec2_no_name_tag(mock_client):
    mock_ec2 = MagicMock()
    mock_ec2.describe_instances.return_value = {
        "Reservations": [
            {
                "Instances": [
                    {
                        "InstanceId": "i-456",
                        "State": {"Name": "stopped"},
                        "InstanceType": "t3.small",
                        "Tags": [],
                    }
                ]
            }
        ]
    }
    mock_client.return_value = mock_ec2

    result = discover_ec2()
    assert result[0].name == "i-456"
