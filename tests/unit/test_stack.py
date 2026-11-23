"""Stack files (ec2sim.stack): validation, and up/down against moto's in-memory EC2."""

import io
from pathlib import Path

import boto3
import pytest
import yaml
from moto import mock_aws

from ec2sim import patches
from ec2sim import stack as st

patches.apply(None)  # ec2-sim's moto fixes (custom SG IDs, IP release on terminate), no Docker

BASE = """
name: shop
region: us-west-2
key_pairs: [me]
instance_profiles: [app-role]
vpcs:
  - name: main
    cidr: 10.7.0.0/16
    subnets:
      - {name: app, cidr: 10.7.1.0/24}
security_groups:
  - name: web
    vpc: main
    ingress:
      - {protocol: all, cidr: 10.7.0.0/16}
      - {port: 80, cidr: 0.0.0.0/0}
      - {ports: 8000-8100, source: web}
instances:
  - name: db
    ami: ami-12c6146b
    subnet: app
    ip: 10.7.1.128
    security_groups: [web]
    user_data: "#!/bin/sh\\necho hi"
    ready: {port: 27017}
  - name: app
    ami: ami-12c6146b
    subnet: app
    count: 2
    key: me
    profile: app-role
    security_groups: [web]
    depends_on: [db]
    tags: {Role: app}
"""


def parse(text: str, tmp_path: Path | None = None) -> st.Stack:
    return st.parse(yaml.safe_load(text), (tmp_path or Path(".")) / "stack.yaml")


def bad(text: str, match: str) -> None:
    with pytest.raises(st.StackError, match=match):
        parse(text)


def test_parse_ok():
    s = parse(BASE)
    assert s.name == "shop" and s.region == "us-west-2"
    assert s.subnets["app"] == {"vpc": "main", "cidr": "10.7.1.0/24", "az": "us-west-2a"}
    rules = s.groups["web"]["rules"]
    assert rules[0].protocol == "-1" and rules[2].from_port == 8000 and rules[2].source == "web"
    assert [i.name for i in s.ordered_instances()] == ["db", "app"]
    assert s.instances[1].names() == ["app-1", "app-2"]


def test_dependency_order_and_cycle():
    s = parse(BASE.replace("depends_on: [db]", "").replace(
        "    ready: {port: 27017}", "    ready: {port: 27017}\n    depends_on: [app]"))
    assert [i.name for i in s.ordered_instances()] == ["app", "db"]
    bad(BASE.replace("    ready: {port: 27017}", "    ready: {port: 27017}\n    depends_on: [app]"),
        "dependency cycle")


@pytest.mark.parametrize("old,new,match", [
    ("cidr: 10.7.1.0/24", "cidr: 10.8.1.0/24", "not inside the VPC"),
    ("ip: 10.7.1.128", "ip: 10.7.1.2", "not a usable address"),
    ("ip: 10.7.1.128", "ip: 10.7.2.5", "not a usable address"),
    ("subnet: app\n    ip", "subnet: nope\n    ip", "no subnet named 'nope'"),
    ("source: web", "source: ghost", "no security group named 'ghost'"),
    ("key: me", "key: you", "no key pair named 'you'"),
    ("depends_on: [db]", "depends_on: [ghost]", "no instance named 'ghost'"),
    ("count: 2", "count: 2\n    ip: 10.7.1.50", "can't be combined with count"),
    ("name: shop", "name: Shop", "lowercase"),
    ("tags: {Role: app}", "tags: {Name: x}", "set by ec2-sim"),
    ("  - name: db\n", "  - name: db\n    colour: red\n", "unknown key"),
])
def test_validation_errors(old, new, match):
    assert old in BASE
    bad(BASE.replace(old, new, 1), match)


def test_variables(monkeypatch):
    monkeypatch.setenv("APP_AMI", "ami-0abc")
    s = parse(BASE.replace("ami: ami-12c6146b\n    subnet: app\n    count",
                           "ami: ${APP_AMI}\n    subnet: app\n    count")
              .replace("region: us-west-2", "region: ${REGION:-eu-west-1}"))
    assert s.instances[1].ami == "ami-0abc" and s.region == "eu-west-1"
    bad(BASE.replace("region: us-west-2", "region: ${NOPE_NOT_SET}"), "NOPE_NOT_SET")


def test_user_data_file(tmp_path):
    (tmp_path / "db.sh").write_text("#!/bin/sh\necho from-file\n")
    s = parse(BASE.replace('user_data: "#!/bin/sh\\necho hi"', "user_data_file: db.sh"), tmp_path)
    assert "from-file" in s.instances[0].user_data


@pytest.fixture
def aws(monkeypatch, tmp_path):
    monkeypatch.setattr(st, "KEYS_DIR", tmp_path / "keys")
    with mock_aws():
        kw = {"region_name": "us-west-2", "aws_access_key_id": "t", "aws_secret_access_key": "t"}
        yield boto3.client("ec2", **kw), boto3.client("iam", **kw)


def applier(aws, text=BASE):
    out = io.StringIO()
    return st.Applier(parse(text), aws[0], aws[1], docker_client=None, out=out, poll=0), out


def test_up_creates_everything_and_is_idempotent(aws, tmp_path):
    ec2, iam = aws
    a, out = applier(aws)
    a.up()
    rows = {r["name"]: r for r in a.status()}
    assert set(rows) == {"db", "app-1", "app-2"}
    assert rows["db"]["ip"] == "10.7.1.128" and rows["db"]["state"] == "running"
    assert (tmp_path / "keys" / "shop" / "me.pem").exists()
    iam.get_instance_profile(InstanceProfileName="app-role")
    sg = ec2.describe_security_groups(Filters=[{"Name": "tag:ec2sim:id", "Values": ["sg/web"]}])["SecurityGroups"][0]
    assert len(sg["IpPermissions"]) == 3
    app = ec2.describe_instances(Filters=[{"Name": "tag:Name", "Values": ["app-1"]}])["Reservations"][0]["Instances"][0]
    assert {"Key": "Role", "Value": "app"} in app["Tags"] and app["KeyName"] == "me"

    # second run: nothing new
    b, out2 = applier(aws)
    b.up()
    assert "launched" not in out2.getvalue() and "created" not in out2.getvalue()
    n = sum(len(r["Instances"]) for r in ec2.describe_instances(
        Filters=[{"Name": "instance-state-name", "Values": ["running"]}])["Reservations"])
    assert n == 3


def test_up_replaces_a_terminated_instance(aws):
    ec2, _ = aws
    a, _ = applier(aws)
    a.up()
    db = a.instance_ids["db"][0]
    ec2.terminate_instances(InstanceIds=[db])
    b, out = applier(aws)
    b.up()
    assert "instance db: launched" in out.getvalue()


def test_custom_group_id(aws):
    ec2, _ = aws
    a, _ = applier(aws, BASE.replace("    vpc: main\n    ingress", "    vpc: main\n    id: sg-138d3374\n    ingress"))
    a.up()
    assert a.group_ids["web"] == "sg-138d3374"


def test_ami_by_name(aws):
    ec2, _ = aws
    r = ec2.run_instances(ImageId="ami-12c6146b", MinCount=1, MaxCount=1)
    old = ec2.create_image(InstanceId=r["Instances"][0]["InstanceId"], Name="shop-app-1")["ImageId"]
    a, _ = applier(aws)
    assert a.resolve_ami({"name": "shop-app-*"}) == old
    with pytest.raises(st.StackError, match="no available AMI"):
        a.resolve_ami({"name": "nothing-*"})
    with pytest.raises(st.StackError, match="not an AMI ID or a base image"):
        a.resolve_ami("windows-95")
    assert a.resolve_ami("ubuntu-18.04").startswith("ami-0ec2511b18")


def test_down_removes_everything(aws, tmp_path):
    ec2, iam = aws
    a, _ = applier(aws)
    a.up()
    vpcs_before = len(ec2.describe_vpcs()["Vpcs"])
    d, out = applier(aws)
    d.down()
    assert len(ec2.describe_vpcs()["Vpcs"]) == vpcs_before - 1
    left = ec2.describe_instances(Filters=[{"Name": "tag:ec2sim:stack", "Values": ["shop"]},
                                           {"Name": "instance-state-name", "Values": st.ALIVE}])
    assert left["Reservations"] == []
    assert not ec2.describe_security_groups(Filters=[{"Name": "tag:ec2sim:stack", "Values": ["shop"]}])["SecurityGroups"]
    assert not (tmp_path / "keys" / "shop" / "me.pem").exists()
    with pytest.raises(Exception):
        iam.get_instance_profile(InstanceProfileName="app-role")
    # and it comes back
    u, _ = applier(aws)
    u.up()
    assert {r["state"] for r in u.status()} == {"running"}
