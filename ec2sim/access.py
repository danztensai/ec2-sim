"""Gateway access control: which instance:port pairs http://<name>.localhost may reach.

security-groups mode (default): an instance is reachable on PORT if one of its
security groups has an inbound rule for TCP (or all traffic) covering PORT from
an IP range (IPv4/IPv6 CIDR). Rules that only reference other security groups
don't count: they describe instance-to-instance traffic, not access from your
computer. A tag `ec2sim:web = 80,9404` (or `none`) overrides the groups.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from . import config

LOG = logging.getLogger("ec2sim.access")
WEB_TAG = "ec2sim:web"


def web_name(name_tag: str | None) -> str | None:
    if not name_tag:
        return None
    safe = "".join(c if c.isalnum() or c in "-." else "-" for c in name_tag.lower()).strip("-.")
    return safe if safe and "." not in safe else None


def _tags(inst: Any) -> dict[str, str]:
    try:
        return {t.get("key"): t.get("value", "") for t in inst.get_tags()}
    except Exception:  # noqa: BLE001
        return {}


def rule_allows(rule: Any, port: int) -> bool:
    proto = str(getattr(rule, "ip_protocol", "") or "")
    if not getattr(rule, "ip_range", None):  # source-group / prefix-list rules: not "from outside"
        return False
    if proto in ("-1", "all"):
        return True
    if proto not in ("tcp", "6"):
        return False
    lo, hi = getattr(rule, "from_port", None), getattr(rule, "to_port", None)
    return lo is not None and hi is not None and int(lo) <= port <= int(hi)


def instance_ports(inst: Any, ports: list[int]) -> set[int]:
    """Gateway ports this instance may be reached on."""
    tag = _tags(inst).get(WEB_TAG)
    if tag is not None:
        if tag.strip().lower() in ("", "none", "off", "false"):
            return set()
        if tag.strip().lower() in ("all", "*"):
            return set(ports)
        wanted = {int(p) for p in tag.replace(" ", "").split(",") if p.isdigit()}
        return {p for p in ports if p in wanted}
    out: set[int] = set()
    for group in getattr(inst, "security_groups", []) or []:
        for rule in getattr(group, "ingress_rules", []) or []:
            out |= {p for p in ports if rule_allows(rule, p)}
    return out


def compute_allowed(ports: list[int]) -> set[str]:
    from moto.ec2.models import ec2_backends

    allowed: set[str] = set()
    for account, regions in list(ec2_backends.items()):
        for region, backend in list(regions.items()):
            for reservation in list(getattr(backend, "reservations", {}).values()):
                for inst in reservation.instances:
                    if inst.state != "running":
                        continue
                    open_ports = instance_ports(inst, ports)
                    if not open_ports:
                        continue
                    names = [inst.id]
                    if (n := web_name(_tags(inst).get("Name"))):
                        names.append(n)
                    allowed |= {f"{n}:{p}" for n in names for p in open_ports}
    return allowed


def start_sync(vm: Any, interval: float = 2.0) -> None:
    """Keep the gateway's allow-map in step with security groups and tags."""
    if config.GATEWAY_ACCESS == "open":
        LOG.info("gateway access: open (every instance, every gateway port)")
        return

    def loop() -> None:
        while True:
            try:
                ports = vm.gateway_ports()
                if ports and vm.update_gateway_access(compute_allowed(ports)):
                    LOG.debug("gateway access updated")
            except Exception:  # noqa: BLE001 - keep syncing
                LOG.exception("gateway access sync failed")
            time.sleep(interval)

    threading.Thread(target=loop, daemon=True, name="gateway-access").start()
    LOG.info("gateway access: security groups (inbound TCP rules from an IP range; tag %s overrides)", WEB_TAG)
