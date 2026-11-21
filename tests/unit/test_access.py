"""Gateway access rules (ec2sim.access) against moto's in-memory EC2."""

import boto3
import pytest
from moto import mock_aws

from ec2sim import access

PORTS = [80, 443, 9404]


@pytest.fixture
def ec2():
    with mock_aws():
        yield boto3.client("ec2", region_name="us-west-2", aws_access_key_id="t", aws_secret_access_key="t")


def launch(ec2, name, groups=None, tags=None):
    vpc = ec2.describe_vpcs()["Vpcs"][0]["VpcId"]
    kw = {"SecurityGroupIds": groups} if groups else {}
    all_tags = [{"Key": "Name", "Value": name}] + [{"Key": k, "Value": v} for k, v in (tags or {}).items()]
    r = ec2.run_instances(ImageId="ami-12c6146b", MinCount=1, MaxCount=1, InstanceType="t3.micro",
                          TagSpecifications=[{"ResourceType": "instance", "Tags": all_tags}], **kw)
    return r["Instances"][0]["InstanceId"], vpc


def group(ec2, vpc, name, perms):
    gid = ec2.create_security_group(GroupName=name, Description=name, VpcId=vpc)["GroupId"]
    if perms:
        ec2.authorize_security_group_ingress(GroupId=gid, IpPermissions=perms)
    return gid


def tcp(lo, hi=None, cidr="0.0.0.0/0"):
    return {"IpProtocol": "tcp", "FromPort": lo, "ToPort": hi or lo, "IpRanges": [{"CidrIp": cidr}]}


def test_default_group_blocks_everything(ec2):
    launch(ec2, "app1")
    assert access.compute_allowed(PORTS) == set()


def test_ip_range_rule_allows_port_by_name_and_id(ec2):
    vpc = ec2.describe_vpcs()["Vpcs"][0]["VpcId"]
    web = group(ec2, vpc, "web", [tcp(80)])
    iid, _ = launch(ec2, "App1", groups=[web])
    assert access.compute_allowed(PORTS) == {"app1:80", f"{iid}:80"}


def test_port_range_and_all_traffic(ec2):
    vpc = ec2.describe_vpcs()["Vpcs"][0]["VpcId"]
    rng = group(ec2, vpc, "range", [tcp(9000, 9500, "10.0.0.0/8")])
    allt = group(ec2, vpc, "all", [{"IpProtocol": "-1", "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}])
    launch(ec2, "app2", groups=[rng])
    launch(ec2, "app3", groups=[allt])
    got = access.compute_allowed(PORTS)
    assert "app2:9404" in got and "app2:80" not in got
    assert {"app3:80", "app3:443", "app3:9404"} <= got


def test_group_to_group_rules_do_not_expose(ec2):
    vpc = ec2.describe_vpcs()["Vpcs"][0]["VpcId"]
    src = group(ec2, vpc, "src", [])
    db = group(ec2, vpc, "db", [{"IpProtocol": "tcp", "FromPort": 80, "ToPort": 80,
                                 "UserIdGroupPairs": [{"GroupId": src}]}])
    launch(ec2, "db", groups=[db])
    assert access.compute_allowed(PORTS) == set()


def test_tag_overrides_groups(ec2):
    vpc = ec2.describe_vpcs()["Vpcs"][0]["VpcId"]
    web = group(ec2, vpc, "web", [tcp(80)])
    launch(ec2, "tagged", tags={"ec2sim:web": "9404"})
    launch(ec2, "hidden", groups=[web], tags={"ec2sim:web": "none"})
    got = access.compute_allowed(PORTS)
    assert "tagged:9404" in got and "tagged:80" not in got
    assert not any(a.startswith("hidden:") for a in got)


def test_stopped_instances_are_not_exposed(ec2):
    vpc = ec2.describe_vpcs()["Vpcs"][0]["VpcId"]
    web = group(ec2, vpc, "web", [tcp(80)])
    iid, _ = launch(ec2, "app1", groups=[web])
    ec2.stop_instances(InstanceIds=[iid])
    assert access.compute_allowed(PORTS) == set()


def test_web_name():
    assert access.web_name("My App_1") == "my-app-1"
    assert access.web_name("db") == "db"
    assert access.web_name("a.b") is None  # dotted names aren't routable as <name>.localhost
    assert access.web_name("") is None
