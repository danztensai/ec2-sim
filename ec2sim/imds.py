"""Instance metadata service (IMDSv1 + IMDSv2) for ec2-sim instances.

Requests reach this server through the proxy container at 169.254.169.254,
which adds X-Forwarded-For with the caller's IMDS-network IP; that IP maps to
an instance container and from there to moto's Instance model.
"""

from __future__ import annotations

import base64
import json
import re
import logging
import secrets
import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from werkzeug.serving import make_server
from werkzeug.wrappers import Request, Response

from . import config

LOG = logging.getLogger("ec2sim.imds")


def _find_instance(info: dict[str, str]):
    from moto.ec2.models import ec2_backends

    backend = ec2_backends[info["account"]][info["region"]]
    for reservation in backend.reservations.values():
        for inst in reservation.instances:
            if inst.id == info["instance_id"]:
                return backend, inst
    return backend, None


def _instance_profile(inst: Any) -> tuple[str | None, str | None]:
    """(instance profile name, role name) for an instance, incl. ASG launch configs."""
    profile = getattr(inst, "iam_instance_profile", None) or getattr(inst, "instance_profile", None)
    if isinstance(profile, dict):
        profile = profile.get("Arn") or profile.get("Name")
    if not profile and getattr(inst, "autoscaling_group", None) is not None:
        lc = getattr(inst.autoscaling_group, "launch_config", None)
        profile = getattr(lc, "iam_instance_profile_name", None) or getattr(lc, "iam_instance_profile", None)
    if not profile:
        return None, None
    name = str(profile).split("/")[-1]
    role = name
    try:
        from moto.iam.models import iam_backends

        iam = iam_backends[inst.ec2_backend.account_id]["global"]
        prof = iam.get_instance_profile(name)
        if prof.roles:
            role = prof.roles[0].name
    except Exception:  # noqa: BLE001 - profile not modelled in moto IAM; use its name
        pass
    return name, role


def _metadata(backend: Any, inst: Any) -> dict[str, Any]:
    az = getattr(inst.placement, "zone", None) or f"{backend.region_name}a"
    local_ip = inst.private_ip or ""
    public_ip = inst.public_ip or ""
    mac = "0e:" + ":".join(inst.id[-10:][i:i + 2] for i in range(0, 10, 2))
    md: dict[str, Any] = {
        "ami-id": inst.image_id,
        "ami-launch-index": "0",
        "hostname": f"ip-{local_ip.replace('.', '-')}.{backend.region_name}.compute.internal",
        "instance-id": inst.id,
        "instance-type": inst.instance_type,
        "local-hostname": f"ip-{local_ip.replace('.', '-')}.{backend.region_name}.compute.internal",
        "local-ipv4": local_ip,
        "mac": mac,
        "placement/availability-zone": az,
        "placement/region": backend.region_name,
        "public-hostname": inst.public_dns_name or "",
        "public-ipv4": public_ip,
        "security-groups": "\n".join(g.name for g in inst.security_groups),
        "services/domain": "amazonaws.com",
    }
    profile, role = _instance_profile(inst)
    if role:
        md["iam/info"] = json.dumps({
            "Code": "Success",
            "LastUpdated": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "InstanceProfileArn": f"arn:aws:iam::{backend.account_id}:instance-profile/{profile}",
            "InstanceProfileId": "AIPA" + inst.id[2:].upper()[:17],
        }, indent=2)
        md[f"iam/security-credentials/{role}"] = None  # generated per request
    return md


def _credentials() -> str:
    now = datetime.now(UTC)
    return json.dumps({
        "Code": "Success",
        "LastUpdated": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "Type": "AWS-HMAC",
        "AccessKeyId": "ASIA" + secrets.token_hex(8).upper(),
        "SecretAccessKey": secrets.token_urlsafe(30),
        "Token": secrets.token_urlsafe(120),
        "Expiration": (now + timedelta(hours=6)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, indent=2)


def _listing(keys: list[str], prefix: str) -> str | None:
    """Directory listing for a metadata path prefix ('' = root)."""
    children: list[str] = []
    for key in keys:
        if prefix and not key.startswith(prefix + "/"):
            continue
        rest = key[len(prefix) + 1:] if prefix else key
        head, _, tail = rest.partition("/")
        entry = head + "/" if tail else head
        if entry not in children:
            children.append(entry)
    return "\n".join(children) if children else None


class ImdsApp:
    def __init__(self, vm: Any) -> None:
        self.vm = vm
        self.tokens: dict[str, datetime] = {}

    def __call__(self, environ, start_response):
        return self.handle(Request(environ))(environ, start_response)

    def handle(self, req: Request) -> Response:
        if req.method == "PUT" and req.path == "/latest/api/token":
            ttl = int(req.headers.get("X-aws-ec2-metadata-token-ttl-seconds", "21600"))
            token = secrets.token_urlsafe(40)
            self.tokens[token] = datetime.now(UTC) + timedelta(seconds=ttl)
            return Response(token, headers={"X-aws-ec2-metadata-token-ttl-seconds": str(ttl)})
        token = req.headers.get("X-aws-ec2-metadata-token")
        if token is not None and self.tokens.get(token, datetime.min.replace(tzinfo=UTC)) < datetime.now(UTC):
            return Response("Unauthorized", status=401)

        caller = (req.headers.get("X-Forwarded-For") or req.remote_addr or "").split(",")[0].strip()
        info = self.vm.instance_for_imds_ip(caller)
        if not info:
            return Response("Not Found", status=404)
        backend, inst = _find_instance(info)
        if inst is None:
            return Response("Not Found", status=404)

        path = req.path.rstrip("/")
        # Real IMDS serves the same tree under /latest and every dated version
        # (/2009-04-04/..., used by ec2metadata); normalise to /latest.
        parts = path.split("/", 2)
        if len(parts) > 1 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", parts[1]):
            path = "/latest" + (("/" + parts[2]) if len(parts) > 2 else "")
        if path == "":
            return Response("latest")
        if path == "/latest":
            return Response("dynamic\nmeta-data\nuser-data")
        if path == "/latest/user-data":
            ud = inst.user_data
            if not ud:
                return Response("Not Found", status=404)
            if isinstance(ud, (list, tuple)):
                ud = ud[0]
            if hasattr(ud, "as_bytes"):  # moto Base64EncodedString
                ud = ud.as_bytes()
            try:
                return Response(base64.b64decode(ud, validate=True))
            except Exception:  # noqa: BLE001 - not base64: serve as-is
                return Response(ud)
        if path == "/latest/dynamic/instance-identity/document":
            return Response(json.dumps({
                "accountId": backend.account_id,
                "architecture": "x86_64",
                "availabilityZone": getattr(inst.placement, "zone", None),
                "imageId": inst.image_id,
                "instanceId": inst.id,
                "instanceType": inst.instance_type,
                "pendingTime": str(inst.launch_time),
                "privateIp": inst.private_ip,
                "region": backend.region_name,
                "version": "2017-09-30",
            }, indent=2), mimetype="application/json")
        if path == "/latest/dynamic":
            return Response("instance-identity/")
        if path == "/latest/dynamic/instance-identity":
            return Response("document")
        if path.startswith("/latest/meta-data"):
            key = path[len("/latest/meta-data"):].lstrip("/")
            md = _metadata(backend, inst)
            if key in md:
                if key.startswith("iam/security-credentials/"):
                    return Response(_credentials())
                return Response(md[key])
            listing = _listing(list(md), key)
            if listing is not None:
                return Response(listing)
        return Response("Not Found", status=404)


def start(vm: Any) -> bool:
    if not config.IMDS_PORT or not vm.ensure_imds(config.IMDS_PORT):
        return False
    server = make_server(config.IMDS_GATEWAY, config.IMDS_PORT, ImdsApp(vm), threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True, name="imds").start()
    LOG.info("IMDS at http://%s/ inside instances (server %s:%s)",
             config.IMDS_ADDRESS, config.IMDS_GATEWAY, config.IMDS_PORT)
    return True
