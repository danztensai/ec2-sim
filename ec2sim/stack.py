"""Stacks: a whole environment in one file.

    ec2-sim up stack.yaml        create what's missing (VPCs, subnets, security groups,
                                 key pairs, instance profiles, instances), wait until ready
    ec2-sim status stack.yaml    what's running, IPs, readiness
    ec2-sim down stack.yaml      remove everything the stack created
    ec2-sim reset stack.yaml     down, then up
    ec2-sim validate stack.yaml  check the file without touching anything

The stack is applied through the EC2/IAM API of a running ec2-sim server (the
same path the AWS CLI takes), so it works for anything ec2-sim supports.
Every resource is tagged `ec2sim:stack=<name>` and `ec2sim:id=<logical name>`,
which makes `up` idempotent and lets `down` find what to remove.

File format (see docs/stacks.md for the full reference):

    name: shop
    region: us-west-2
    vpcs:
      - name: main
        cidr: 10.7.0.0/16
        subnets:
          - {name: app, cidr: 10.7.1.0/24}
    security_groups:
      - name: stack
        vpc: main
        ingress:
          - {protocol: all, cidr: 10.7.0.0/16}
          - {port: 80, cidr: 0.0.0.0/0}
    instances:
      - name: db
        ami: ubuntu-18.04
        subnet: app
        ip: 10.7.1.128
        security_groups: [stack]
        user_data_file: db.sh
        ready: {port: 27017, timeout: 300}
      - name: app
        ami: {name: "my-app-*"}
        subnet: app
        security_groups: [stack]
        depends_on: [db]
"""

from __future__ import annotations

import ipaddress
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import config

STACK_TAG = "ec2sim:stack"
ID_TAG = "ec2sim:id"
KEYS_DIR = Path.home() / ".ec2-sim" / "stacks"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
ALIVE = ["pending", "running", "stopping", "stopped"]


class StackError(Exception):
    """A problem with the stack file or its environment (printed without a traceback)."""


# --------------------------------------------------------------------------- model
@dataclass
class Rule:
    protocol: str  # tcp | udp | icmp | -1
    from_port: int
    to_port: int
    cidrs: list[str] = field(default_factory=list)
    source: str | None = None  # logical security group name

    def permission(self, group_ids: dict[str, str]) -> dict[str, Any]:
        p: dict[str, Any] = {"IpProtocol": self.protocol}
        if self.protocol != "-1":
            p["FromPort"], p["ToPort"] = self.from_port, self.to_port
        if self.cidrs:
            p["IpRanges"] = [{"CidrIp": c} for c in self.cidrs]
        if self.source:
            p["UserIdGroupPairs"] = [{"GroupId": group_ids[self.source]}]
        return p


@dataclass
class Instance:
    name: str
    ami: str | dict[str, str]
    type: str
    subnet: str
    count: int = 1
    ip: str | None = None
    security_groups: list[str] = field(default_factory=list)
    key: str | None = None
    profile: str | None = None
    user_data: str | None = None
    tags: dict[str, str] = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)
    ready: dict[str, Any] | None = None
    forwards: list[dict[str, int]] = field(default_factory=list)

    def names(self) -> list[str]:
        """Name tags of the instances (name, or name-1..n when count > 1)."""
        return [self.name] if self.count == 1 else [f"{self.name}-{i}" for i in range(1, self.count + 1)]


@dataclass
class Stack:
    name: str
    region: str
    path: Path
    vpcs: list[dict[str, Any]]
    subnets: dict[str, dict[str, Any]]  # name -> {vpc, cidr, az}
    groups: dict[str, dict[str, Any]]  # name -> {vpc, description, id, rules: [Rule]}
    key_pairs: list[dict[str, Any]]
    profiles: list[str]
    instances: list[Instance]

    def ordered_instances(self) -> list[Instance]:
        """Instances with every dependency before its dependents (file order otherwise)."""
        by_name = {i.name: i for i in self.instances}
        out: list[Instance] = []
        state: dict[str, int] = {}

        def visit(i: Instance, chain: list[str]) -> None:
            if state.get(i.name) == 2:
                return
            if state.get(i.name) == 1:
                raise StackError("dependency cycle: " + " -> ".join(chain + [i.name]))
            state[i.name] = 1
            for d in i.depends_on:
                visit(by_name[d], chain + [i.name])
            state[i.name] = 2
            out.append(i)

        for i in self.instances:
            visit(i, [])
        return out


# ------------------------------------------------------------------------- loading
_VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def _expand(value: Any, where: str) -> Any:
    """${VAR} and ${VAR:-default} from the environment, in string values only."""
    if isinstance(value, str):
        def sub(m: re.Match) -> str:
            name, default = m.group(1), m.group(2)
            if name in os.environ:
                return os.environ[name]
            if default is not None:
                return default
            raise StackError(f"{where}: variable ${{{name}}} is not set")

        return _VAR.sub(sub, value)
    if isinstance(value, list):
        return [_expand(v, f"{where}[{n}]") for n, v in enumerate(value)]
    if isinstance(value, dict):
        return {k: _expand(v, f"{where}.{k}") for k, v in value.items()}
    return value


def _ports(rule: dict[str, Any], where: str) -> tuple[int, int]:
    if "port" in rule:
        p = int(rule["port"])
        return p, p
    if "ports" in rule:
        lo, _, hi = str(rule["ports"]).partition("-")
        return int(lo), int(hi or lo)
    raise StackError(f"{where}: give port or ports (or protocol: all)")


def _need(d: dict[str, Any], key: str, where: str) -> Any:
    if key not in d or d[key] in (None, ""):
        raise StackError(f"{where}: missing '{key}'")
    return d[key]


def _only(d: dict[str, Any], allowed: set[str], where: str) -> None:
    extra = set(d) - allowed
    if extra:
        raise StackError(f"{where}: unknown key(s) {', '.join(sorted(extra))} "
                         f"(allowed: {', '.join(sorted(allowed))})")


def _cidr(value: str, where: str) -> ipaddress.IPv4Network:
    try:
        return ipaddress.ip_network(value, strict=True)  # type: ignore[return-value]
    except ValueError as e:
        raise StackError(f"{where}: {e}") from None


def parse(data: Any, path: Path) -> Stack:
    """Validate a loaded stack document and build the model. Raises StackError."""
    if not isinstance(data, dict):
        raise StackError(f"{path}: expected a mapping at the top level")
    data = _expand(data, path.name)
    _only(data, {"name", "region", "vpcs", "security_groups", "key_pairs", "instance_profiles",
                 "instances"}, path.name)
    name = str(_need(data, "name", path.name))
    if not NAME_RE.match(name):
        raise StackError(f"name: '{name}' must be lowercase letters, digits and dashes")
    region = str(data.get("region") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1")

    vpcs: list[dict[str, Any]] = []
    subnets: dict[str, dict[str, Any]] = {}
    for n, v in enumerate(data.get("vpcs") or []):
        where = f"vpcs[{n}]"
        _only(v, {"name", "cidr", "subnets"}, where)
        vname, vnet = str(_need(v, "name", where)), _cidr(_need(v, "cidr", where), f"{where}.cidr")
        if any(x["name"] == vname for x in vpcs):
            raise StackError(f"{where}: duplicate VPC name '{vname}'")
        vpcs.append({"name": vname, "cidr": str(vnet)})
        for m, s in enumerate(v.get("subnets") or []):
            sw = f"{where}.subnets[{m}]"
            _only(s, {"name", "cidr", "az"}, sw)
            sname, snet = str(_need(s, "name", sw)), _cidr(_need(s, "cidr", sw), f"{sw}.cidr")
            if not snet.subnet_of(vnet):  # type: ignore[arg-type]
                raise StackError(f"{sw}: {snet} is not inside the VPC's {vnet}")
            if sname in subnets:
                raise StackError(f"{sw}: duplicate subnet name '{sname}' (subnet names are stack-wide)")
            subnets[sname] = {"vpc": vname, "cidr": str(snet), "az": s.get("az") or f"{region}a"}

    groups: dict[str, dict[str, Any]] = {}
    raw_groups = data.get("security_groups") or []
    group_names = {g.get("name") for g in raw_groups if isinstance(g, dict)}
    for n, g in enumerate(raw_groups):
        where = f"security_groups[{n}]"
        _only(g, {"name", "vpc", "description", "id", "ingress"}, where)
        gname = str(_need(g, "name", where))
        if gname in groups:
            raise StackError(f"{where}: duplicate security group name '{gname}'")
        gvpc = str(_need(g, "vpc", where))
        if gvpc not in {v["name"] for v in vpcs}:
            raise StackError(f"{where}.vpc: no VPC named '{gvpc}'")
        rules = []
        for m, r in enumerate(g.get("ingress") or []):
            rw = f"{where}.ingress[{m}]"
            _only(r, {"protocol", "port", "ports", "cidr", "source"}, rw)
            proto = str(r.get("protocol", "tcp")).lower()
            proto = {"all": "-1", "-1": "-1"}.get(proto, proto)
            if proto not in ("tcp", "udp", "icmp", "-1"):
                raise StackError(f"{rw}.protocol: use tcp, udp, icmp or all")
            lo, hi = (-1, -1) if proto == "-1" else _ports(r, rw)
            cidrs = r.get("cidr") or []
            cidrs = [cidrs] if isinstance(cidrs, str) else list(cidrs)
            for c in cidrs:
                _cidr(c, f"{rw}.cidr")
            src = r.get("source")
            if src is not None and src not in group_names:
                raise StackError(f"{rw}.source: no security group named '{src}'")
            if not cidrs and not src:
                raise StackError(f"{rw}: give cidr or source")
            rules.append(Rule(proto, lo, hi, cidrs, src))
        gid = g.get("id")
        if gid is not None and not re.match(r"^sg-[0-9a-f]{8,17}$", str(gid)):
            raise StackError(f"{where}.id: '{gid}' is not a security group ID (sg-…)")
        groups[gname] = {"vpc": gvpc, "description": g.get("description") or f"{name}/{gname}",
                         "id": gid, "rules": rules}

    key_pairs = []
    for n, k in enumerate(data.get("key_pairs") or []):
        where = f"key_pairs[{n}]"
        if isinstance(k, str):
            k = {"name": k}
        _only(k, {"name", "public_key_file"}, where)
        kp = {"name": str(_need(k, "name", where)), "public_key_file": None}
        if k.get("public_key_file"):
            f = Path(os.path.expanduser(k["public_key_file"]))
            kp["public_key_file"] = f if f.is_absolute() else path.parent / f
        key_pairs.append(kp)
    profiles = [str(p if isinstance(p, str) else _need(p, "name", f"instance_profiles[{n}]"))
                for n, p in enumerate(data.get("instance_profiles") or [])]

    instances: list[Instance] = []
    raw_inst = data.get("instances") or []
    inst_names = [i.get("name") for i in raw_inst if isinstance(i, dict)]
    for n, i in enumerate(raw_inst):
        where = f"instances[{n}]"
        _only(i, {"name", "ami", "type", "subnet", "count", "ip", "security_groups", "key", "profile",
                  "user_data", "user_data_file", "tags", "depends_on", "ready", "forwards"}, where)
        iname = str(_need(i, "name", where))
        if not NAME_RE.match(iname):
            raise StackError(f"{where}.name: '{iname}' must be lowercase letters, digits and dashes "
                             "(it becomes the instance's DNS name)")
        if inst_names.count(iname) > 1:
            raise StackError(f"{where}: duplicate instance name '{iname}'")
        subnet = str(_need(i, "subnet", where))
        if subnet not in subnets:
            raise StackError(f"{where}.subnet: no subnet named '{subnet}'")
        ami = _need(i, "ami", where)
        if isinstance(ami, dict):
            _only(ami, {"name", "owner"}, f"{where}.ami")
            _need(ami, "name", f"{where}.ami")
        count = int(i.get("count", 1))
        if count < 1:
            raise StackError(f"{where}.count: must be at least 1")
        ip = i.get("ip")
        if ip is not None:
            if count > 1:
                raise StackError(f"{where}: 'ip' can't be combined with count > 1")
            try:
                addr = ipaddress.ip_address(ip)
            except ValueError as e:
                raise StackError(f"{where}.ip: {e}") from None
            net = ipaddress.ip_network(subnets[subnet]["cidr"])
            hosts = list(net.hosts())
            # AWS reserves the first four addresses and the last one of every subnet.
            if addr not in net or addr == net.network_address or addr in hosts[:3] \
                    or addr == net.broadcast_address:
                raise StackError(f"{where}.ip: {ip} is not a usable address in subnet {subnet} ({net})")
        sgs = list(i.get("security_groups") or [])
        for s in sgs:
            if s not in groups:
                raise StackError(f"{where}.security_groups: no security group named '{s}'")
            if groups[s]["vpc"] != subnets[subnet]["vpc"]:
                raise StackError(f"{where}: security group '{s}' is in another VPC than subnet '{subnet}'")
        key = i.get("key")
        if key is not None and key not in {k["name"] for k in key_pairs}:
            raise StackError(f"{where}.key: no key pair named '{key}' under key_pairs")
        profile = i.get("profile")
        if profile is not None and profile not in profiles:
            raise StackError(f"{where}.profile: no instance profile named '{profile}' under instance_profiles")
        if i.get("user_data") and i.get("user_data_file"):
            raise StackError(f"{where}: give user_data or user_data_file, not both")
        user_data = i.get("user_data")
        if i.get("user_data_file"):
            f = Path(os.path.expanduser(i["user_data_file"]))
            f = f if f.is_absolute() else path.parent / f
            try:
                user_data = f.read_text()
            except OSError as e:
                raise StackError(f"{where}.user_data_file: {e}") from None
        deps = list(i.get("depends_on") or [])
        for d in deps:
            if d not in inst_names:
                raise StackError(f"{where}.depends_on: no instance named '{d}'")
        ready = i.get("ready")
        if ready is not None:
            _only(ready, {"port", "command", "timeout"}, f"{where}.ready")
            if not ready.get("port") and not ready.get("command"):
                raise StackError(f"{where}.ready: give port or command")
        forwards = []
        for m, fw in enumerate(i.get("forwards") or []):
            fw = {"port": fw} if isinstance(fw, int) else fw
            _only(fw, {"port", "local"}, f"{where}.forwards[{m}]")
            port = int(_need(fw, "port", f"{where}.forwards[{m}]"))
            forwards.append({"port": port, "local": int(fw.get("local") or port)})
        if forwards and count > 1:
            raise StackError(f"{where}: forwards can't be combined with count > 1")
        tags = {str(k): str(v) for k, v in (i.get("tags") or {}).items()}
        for t in ("Name", STACK_TAG, ID_TAG):
            if t in tags:
                raise StackError(f"{where}.tags: '{t}' is set by ec2-sim")
        instances.append(Instance(
            name=iname, ami=ami, type=str(i.get("type") or "t3.micro"), subnet=subnet, count=count,
            ip=ip, security_groups=sgs, key=key, profile=profile, user_data=user_data, tags=tags,
            depends_on=deps, ready=ready, forwards=forwards,
        ))

    stack = Stack(name, region, path, vpcs, subnets, groups, key_pairs, profiles, instances)
    stack.ordered_instances()  # raises on cycles
    return stack


def load(path: str | Path) -> Stack:
    import yaml

    p = Path(path)
    try:
        text = p.read_text()
    except OSError as e:
        raise StackError(str(e)) from None
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise StackError(f"{p}: {e}") from None
    return parse(data, p)


# ------------------------------------------------------------------------- applying
def _tags(stack: Stack, logical: str, name: str | None = None, extra: dict[str, str] | None = None):
    tags = [{"Key": STACK_TAG, "Value": stack.name}, {"Key": ID_TAG, "Value": logical}]
    if name:
        tags.append({"Key": "Name", "Value": name})
    tags += [{"Key": k, "Value": v} for k, v in (extra or {}).items()]
    return tags


def _filters(stack: Stack, logical: str | None = None) -> list[dict[str, Any]]:
    f = [{"Name": f"tag:{STACK_TAG}", "Values": [stack.name]}]
    if logical is not None:
        f.append({"Name": f"tag:{ID_TAG}", "Values": [logical]})
    return f


def _tag(resource: dict[str, Any], key: str) -> str | None:
    return next((t["Value"] for t in resource.get("Tags") or [] if t["Key"] == key), None)


def _code(e: Exception) -> str:
    return getattr(e, "response", {}).get("Error", {}).get("Code", "")


class Applier:
    """Applies a Stack through boto3 clients (ec2, iam). `docker` is optional: without
    it, readiness checks and port forwards are skipped (e.g. mock mode, unit tests)."""

    def __init__(self, stack: Stack, ec2: Any, iam: Any, docker_client: Any = None,
                 out=sys.stdout, poll: float = 2.0) -> None:
        self.s, self.ec2, self.iam, self.docker, self.out, self.poll = stack, ec2, iam, docker_client, out, poll
        self.vpc_ids: dict[str, str] = {}
        self.subnet_ids: dict[str, str] = {}
        self.group_ids: dict[str, str] = {}
        self.instance_ids: dict[str, list[str]] = {}
        self.ready: set[str] = set()

    def say(self, msg: str) -> None:
        print(msg, file=self.out, flush=True)

    # ---------------------------------------------------------------- lookups
    def _find(self, describe: str, key: str, logical: str) -> dict[str, Any] | None:
        items = getattr(self.ec2, describe)(Filters=_filters(self.s, logical))[key]
        return items[0] if items else None

    def _alive(self, logical: str) -> list[dict[str, Any]]:
        res = self.ec2.describe_instances(Filters=_filters(self.s, logical) + [
            {"Name": "instance-state-name", "Values": ALIVE}])["Reservations"]
        return sorted((i for r in res for i in r["Instances"]), key=lambda i: _tag(i, "Name") or "")

    def resolve_ami(self, ami: str | dict[str, str]) -> str:
        if isinstance(ami, str):
            if ami.startswith("ami-"):
                return ami
            from .catalog import BASE_AMIS

            for b in BASE_AMIS:
                if b.image_dir == ami:
                    return b.ami_id
            known = ", ".join(sorted({b.image_dir for b in BASE_AMIS}))
            raise StackError(f"ami '{ami}': not an AMI ID or a base image ({known})")
        kw: dict[str, Any] = {"Filters": [{"Name": "name", "Values": [ami["name"]]}]}
        if ami.get("owner"):
            kw["Owners"] = [ami["owner"]]
        images = [i for i in self.ec2.describe_images(**kw)["Images"] if i.get("State", "available") == "available"]
        if not images:
            raise StackError(f"no available AMI matches name '{ami['name']}'")
        return max(images, key=lambda i: i.get("CreationDate") or "")["ImageId"]

    # ------------------------------------------------------------------- up
    def up(self, wait: bool = True) -> None:
        s = self.s
        self.say(f"# stack {s.name} ({s.region})")
        for kp in s.key_pairs:
            self._key_pair(kp)
        for p in s.profiles:
            self._profile(p)
        for v in s.vpcs:
            self._vpc(v)
        for sname, sub in s.subnets.items():
            self._subnet(sname, sub)
        created = [g for g in s.groups if self._group(g)]
        for g in created:
            for r in s.groups[g]["rules"]:
                try:
                    self.ec2.authorize_security_group_ingress(
                        GroupId=self.group_ids[g], IpPermissions=[r.permission(self.group_ids)])
                except Exception as e:  # noqa: BLE001
                    if _code(e) != "InvalidPermission.Duplicate":
                        raise
        for inst in s.ordered_instances():
            for d in inst.depends_on:
                self._wait_ready(next(i for i in s.instances if i.name == d))
            self._instance(inst)
        for inst in s.instances:
            if inst.forwards:
                self._forwards(inst)
        if wait:
            for inst in s.instances:
                self._wait_ready(inst)
        self.say(f"# stack {s.name} is up")

    def _key_pair(self, kp: dict[str, Any]) -> None:
        name = kp["name"]
        try:
            self.ec2.describe_key_pairs(KeyNames=[name])
            self.say(f"  key pair {name}: exists")
            return
        except Exception as e:  # noqa: BLE001
            if _code(e) != "InvalidKeyPair.NotFound":
                raise
        if kp["public_key_file"]:
            material = Path(kp["public_key_file"]).read_bytes()
            self.ec2.import_key_pair(KeyName=name, PublicKeyMaterial=material)
            self.say(f"  key pair {name}: imported {kp['public_key_file']}")
            return
        pem = self.ec2.create_key_pair(KeyName=name)["KeyMaterial"]
        d = KEYS_DIR / self.s.name
        d.mkdir(parents=True, exist_ok=True)
        f = d / f"{name}.pem"
        f.write_text(pem)
        f.chmod(0o600)
        self.say(f"  key pair {name}: created, private key {f}")

    def _profile(self, name: str) -> None:
        try:
            self.iam.get_instance_profile(InstanceProfileName=name)
            self.say(f"  instance profile {name}: exists")
            return
        except Exception as e:  # noqa: BLE001
            if _code(e) != "NoSuchEntity":
                raise
        doc = ('{"Version":"2012-10-17","Statement":[{"Effect":"Allow",'
               '"Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}')
        try:
            self.iam.create_role(RoleName=name, AssumeRolePolicyDocument=doc)
        except Exception as e:  # noqa: BLE001
            if _code(e) != "EntityAlreadyExists":
                raise
        self.iam.create_instance_profile(InstanceProfileName=name)
        self.iam.add_role_to_instance_profile(InstanceProfileName=name, RoleName=name)
        self.say(f"  instance profile {name}: created (role {name})")

    def _vpc(self, v: dict[str, Any]) -> None:
        logical = f"vpc/{v['name']}"
        found = self._find("describe_vpcs", "Vpcs", logical)
        if found:
            self.vpc_ids[v["name"]] = found["VpcId"]
            self.say(f"  vpc {v['name']}: exists ({found['VpcId']})")
            return
        vpc = self.ec2.create_vpc(CidrBlock=v["cidr"], TagSpecifications=[
            {"ResourceType": "vpc", "Tags": _tags(self.s, logical, f"{self.s.name}-{v['name']}")}])["Vpc"]
        self.vpc_ids[v["name"]] = vpc["VpcId"]
        self.say(f"  vpc {v['name']}: created {vpc['VpcId']} {v['cidr']}")

    def _subnet(self, name: str, sub: dict[str, Any]) -> None:
        logical = f"subnet/{name}"
        found = self._find("describe_subnets", "Subnets", logical)
        if found:
            self.subnet_ids[name] = found["SubnetId"]
            self.say(f"  subnet {name}: exists ({found['SubnetId']})")
            return
        r = self.ec2.create_subnet(VpcId=self.vpc_ids[sub["vpc"]], CidrBlock=sub["cidr"],
                                   AvailabilityZone=sub["az"], TagSpecifications=[
            {"ResourceType": "subnet", "Tags": _tags(self.s, logical, f"{self.s.name}-{name}")}])["Subnet"]
        self.subnet_ids[name] = r["SubnetId"]
        self.say(f"  subnet {name}: created {r['SubnetId']} {sub['cidr']} ({sub['az']})")

    def _group(self, name: str) -> bool:
        """Ensure the group exists; True if it was created now (rules still to add)."""
        g = self.s.groups[name]
        logical = f"sg/{name}"
        found = self._find("describe_security_groups", "SecurityGroups", logical)
        if found:
            self.group_ids[name] = found["GroupId"]
            self.say(f"  security group {name}: exists ({found['GroupId']})")
            return False
        tags = _tags(self.s, logical, f"{self.s.name}-{name}")
        if g["id"]:
            tags.append({"Key": "_custom_id_", "Value": g["id"]})
        gid = self.ec2.create_security_group(
            GroupName=f"{self.s.name}-{name}", Description=g["description"], VpcId=self.vpc_ids[g["vpc"]],
            TagSpecifications=[{"ResourceType": "security-group", "Tags": tags}])["GroupId"]
        self.group_ids[name] = gid
        self.say(f"  security group {name}: created {gid}")
        return True

    def _instance(self, inst: Instance) -> None:
        logical = f"instance/{inst.name}"
        alive = self._alive(logical)
        have = {_tag(i, "Name") for i in alive}
        self.instance_ids[inst.name] = [i["InstanceId"] for i in alive]
        missing = [n for n in inst.names() if n not in have]
        if not missing:
            ids = ", ".join(f"{_tag(i, 'Name')}={i['InstanceId']} ({i['State']['Name']})" for i in alive)
            self.say(f"  instance {inst.name}: exists ({ids})")
            return
        image_id = self.resolve_ami(inst.ami)
        for name in missing:
            kw: dict[str, Any] = {
                "ImageId": image_id, "InstanceType": inst.type, "MinCount": 1, "MaxCount": 1,
                "SubnetId": self.subnet_ids[inst.subnet],
                "TagSpecifications": [{"ResourceType": "instance",
                                       "Tags": _tags(self.s, logical, name, inst.tags)}],
            }
            if inst.security_groups:
                kw["SecurityGroupIds"] = [self.group_ids[g] for g in inst.security_groups]
            if inst.ip:
                kw["PrivateIpAddress"] = inst.ip
            if inst.key:
                kw["KeyName"] = inst.key
            if inst.profile:
                kw["IamInstanceProfile"] = {"Name": inst.profile}
            if inst.user_data:
                kw["UserData"] = inst.user_data
            r = self.ec2.run_instances(**kw)["Instances"][0]
            self.instance_ids[inst.name].append(r["InstanceId"])
            ip = r.get("PrivateIpAddress") or "?"
            self.say(f"  instance {name}: launched {r['InstanceId']} at {ip} ({image_id}, {inst.type})")
            if inst.ip and ip != inst.ip:
                self.say(f"    warning: asked for {inst.ip}, got {ip}")

    def _forwards(self, inst: Instance) -> None:
        if self.docker is None:
            self.say(f"  forwards for {inst.name}: skipped (no Docker)")
            return
        from .vm import DockerVMManager

        vm = DockerVMManager.__new__(DockerVMManager)  # only the Docker client is needed
        vm.client = vm.slow_client = self.docker
        iid = self.instance_ids[inst.name][0]
        existing = {(f["instance_id"], f["remote_port"], f["local_port"]) for f in vm.list_forwards()}
        for fw in inst.forwards:
            if (iid, fw["port"], fw["local"]) in existing:
                continue
            try:
                f = vm.add_forward(iid, fw["port"], fw["local"])
                self.say(f"  forward {f['bind']}:{f['local_port']} -> {inst.name}:{fw['port']}")
            except RuntimeError as e:
                self.say(f"  forward {inst.name}:{fw['port']}: {e}")

    # -------------------------------------------------------------- readiness
    def check(self, inst: Instance, iid: str) -> bool:
        """One readiness probe, run inside the instance (works in every network mode)."""
        if self.docker is None or not inst.ready:
            return True
        try:
            c = self.docker.containers.get(f"{config.PREFIX}-{iid}")
        except Exception:  # noqa: BLE001
            return False
        if c.status != "running":
            return False
        if inst.ready.get("port"):
            port = int(inst.ready["port"])
            cmd = ["sh", "-c", f"(command -v bash >/dev/null && bash -c 'exec 3<>/dev/tcp/127.0.0.1/{port}') "
                               f"|| nc -z 127.0.0.1 {port}"]
        else:
            cmd = ["sh", "-c", str(inst.ready["command"])]
        try:
            return c.exec_run(cmd, demux=False).exit_code == 0
        except Exception:  # noqa: BLE001
            return False

    def _wait_ready(self, inst: Instance) -> None:
        if inst.name in self.ready:
            return
        ids = self.instance_ids.get(inst.name) or [i["InstanceId"] for i in self._alive(f"instance/{inst.name}")]
        if not inst.ready or self.docker is None:
            self.ready.add(inst.name)
            return
        timeout = float(inst.ready.get("timeout", 300))
        what = f"port {inst.ready['port']}" if inst.ready.get("port") else "command"
        self.say(f"  waiting for {inst.name} ({what}, up to {timeout:.0f}s)…")
        start = time.monotonic()
        pending = list(ids)
        while pending:
            pending = [i for i in pending if not self.check(inst, i)]
            if not pending:
                break
            if time.monotonic() - start > timeout:
                hint = f"docker exec {config.PREFIX}-{pending[0]} cat /var/log/cloud-init-output.log"
                raise StackError(f"{inst.name} not ready after {timeout:.0f}s (see: {hint})")
            time.sleep(self.poll)
        self.say(f"  {inst.name}: ready after {time.monotonic() - start:.0f}s")
        self.ready.add(inst.name)

    # ----------------------------------------------------------------- status
    def status(self) -> list[dict[str, str]]:
        rows = []
        for inst in self.s.instances:
            alive = {_tag(i, "Name"): i for i in self._alive(f"instance/{inst.name}")}
            for name in inst.names():
                i = alive.get(name)
                if i is None:
                    rows.append({"name": name, "id": "-", "state": "missing", "ip": "-", "ready": "-"})
                    continue
                state = i["State"]["Name"]
                ready = "-"
                if inst.ready and self.docker is not None and state == "running":
                    ready = "yes" if self.check(inst, i["InstanceId"]) else "no"
                rows.append({"name": name, "id": i["InstanceId"], "state": state,
                             "ip": i.get("PrivateIpAddress") or "-", "ready": ready})
        return rows

    # ------------------------------------------------------------------- down
    def down(self) -> None:
        s = self.s
        self.say(f"# removing stack {s.name}")
        res = self.ec2.describe_instances(Filters=_filters(s) + [
            {"Name": "instance-state-name", "Values": ALIVE}])["Reservations"]
        ids = [i["InstanceId"] for r in res for i in r["Instances"]]
        if ids:
            self.ec2.terminate_instances(InstanceIds=ids)
            self.say(f"  terminated {len(ids)} instance(s): {', '.join(ids)}")
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                left = self.ec2.describe_instances(InstanceIds=ids)["Reservations"]
                if all(i["State"]["Name"] == "terminated" for r in left for i in r["Instances"]):
                    break
                time.sleep(self.poll)
        groups = self.ec2.describe_security_groups(Filters=_filters(s))["SecurityGroups"]
        for g in groups:  # drop rules first: groups may reference each other
            if g.get("IpPermissions"):
                self.ec2.revoke_security_group_ingress(GroupId=g["GroupId"], IpPermissions=g["IpPermissions"])
        for g in groups:
            self.ec2.delete_security_group(GroupId=g["GroupId"])
            self.say(f"  deleted security group {_tag(g, ID_TAG)} ({g['GroupId']})")
        for sub in self.ec2.describe_subnets(Filters=_filters(s))["Subnets"]:
            self.ec2.delete_subnet(SubnetId=sub["SubnetId"])
            self.say(f"  deleted subnet {_tag(sub, ID_TAG)} ({sub['SubnetId']})")
        for v in self.ec2.describe_vpcs(Filters=_filters(s))["Vpcs"]:
            self.ec2.delete_vpc(VpcId=v["VpcId"])
            self.say(f"  deleted vpc {_tag(v, ID_TAG)} ({v['VpcId']})")
        for kp in s.key_pairs:
            try:
                self.ec2.delete_key_pair(KeyName=kp["name"])
                self.say(f"  deleted key pair {kp['name']}")
            except Exception:  # noqa: BLE001
                pass
            (KEYS_DIR / s.name / f"{kp['name']}.pem").unlink(missing_ok=True)
        try:
            (KEYS_DIR / s.name).rmdir()  # only if empty
        except OSError:
            pass
        for p in s.profiles:
            try:
                self.iam.remove_role_from_instance_profile(InstanceProfileName=p, RoleName=p)
            except Exception:  # noqa: BLE001
                pass
            for call, kw in ((self.iam.delete_instance_profile, {"InstanceProfileName": p}),
                             (self.iam.delete_role, {"RoleName": p})):
                try:
                    call(**kw)
                except Exception:  # noqa: BLE001
                    pass
            self.say(f"  deleted instance profile {p}")
        self.say(f"# stack {s.name} removed")


# ---------------------------------------------------------------------------- CLI
def _clients(stack: Stack, endpoint: str):
    import boto3

    kw = {"endpoint_url": endpoint, "region_name": stack.region,
          "aws_access_key_id": "test", "aws_secret_access_key": "test"}
    return boto3.client("ec2", **kw), boto3.client("iam", **kw)


def _docker_client():
    try:
        import docker

        c = docker.from_env()
        c.ping()
        return c
    except Exception:  # noqa: BLE001
        return None


def main(action: str, path: str, endpoint: str | None = None, wait: bool = True, as_json: bool = False) -> int:
    try:
        stack = load(path)
        if action == "validate":
            n = sum(i.count for i in stack.instances)
            print(f"{path}: ok (stack {stack.name}: {len(stack.vpcs)} VPC(s), {len(stack.subnets)} subnet(s), "
                  f"{len(stack.groups)} security group(s), {n} instance(s))")
            return 0
        endpoint = endpoint or f"http://127.0.0.1:{config.PORT}"
        from .run import _server_up

        if endpoint.endswith(f":{config.PORT}") and not _server_up():
            raise StackError(f"ec2-sim is not running on port {config.PORT}; start it first (`uv run ec2-sim`)")
        ec2, iam = _clients(stack, endpoint)
        a = Applier(stack, ec2, iam, _docker_client())
        if action == "up":
            a.up(wait=wait)
            action = "status"
        elif action == "down":
            a.down()
            return 0
        elif action == "reset":
            a.down()
            a.up(wait=wait)
            action = "status"
        if action == "status":
            rows = a.status()
            if as_json:
                import json

                print(json.dumps(rows))
            else:
                print(f"{'NAME':<20} {'INSTANCE':<20} {'STATE':<10} {'IP':<16} READY")
                for r in rows:
                    print(f"{r['name']:<20} {r['id']:<20} {r['state']:<10} {r['ip']:<16} {r['ready']}")
        return 0
    except StackError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
