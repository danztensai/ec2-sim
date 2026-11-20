"""Docker-backed VM manager: instances are containers, AMIs are images.

Docker objects carry `ec2sim.*` labels so state can be rediscovered:
  instance containers: ec2sim.instance_id / account / region
  AMI images (ec2sim/ami:<ami-id>): ec2sim.ami_id / account / region / name / ...
"""

from __future__ import annotations

import io
import ipaddress
import json
import logging
import threading
from typing import Any

import docker
from docker.errors import APIError, ImageNotFound, NotFound

from . import config
from .catalog import BASE_AMIS, BaseAmi

LOG = logging.getLogger("ec2sim.vm")


def _label(key: str) -> str:
    return f"{config.LABEL}.{key}"


class DockerVMManager:
    def __init__(self) -> None:
        self.client = docker.from_env()
        self.client.ping()
        self._build_lock = threading.Lock()
        self._net_lock = threading.Lock()
        self._built: set[str] = set()
        self.imds_network = None

    # ------------------------------------------------------------ base images
    def ensure_base_image(self, base: BaseAmi) -> str:
        """Build the Docker image behind a base AMI if it is missing."""
        ref = base.docker_image
        if ref in self._built:
            return ref
        with self._build_lock:
            if ref in self._built:
                return ref
            try:
                self.client.images.get(ref)
            except ImageNotFound:
                LOG.info("building base AMI image %s (first use, may take a minute)", ref)
                self.client.images.build(
                    path=str(config.IMAGES_DIR),
                    dockerfile=f"{base.image_dir}/Dockerfile",
                    tag=ref,
                    rm=True,
                    labels={_label("base_ami_id"): base.ami_id},
                )
                LOG.info("built %s", ref)
            self._built.add(ref)
        return ref

    def build_all_base_images(self) -> None:
        for base in BASE_AMIS:
            try:
                self.ensure_base_image(base)
            except Exception:  # noqa: BLE001 - keep the server up; RunInstances retries
                LOG.exception("failed to build %s", base.docker_image)

    # ---------------------------------------------------------------- network
    def _network_for_subnet(self, subnet_id: str | None, cidr: str | None) -> tuple[Any, bool]:
        """Return (network, cidr_matches). Falls back to a shared network on overlap."""
        with self._net_lock:
            if subnet_id and cidr:
                name = f"{config.PREFIX}-{subnet_id}"
                try:
                    return self.client.networks.get(name), True
                except NotFound:
                    pass
                try:
                    ipam = docker.types.IPAMConfig(
                        pool_configs=[docker.types.IPAMPool(subnet=cidr)]
                    )
                    net = self.client.networks.create(
                        name,
                        driver="bridge",
                        ipam=ipam,
                        labels={_label("subnet_id"): subnet_id},
                    )
                    return net, True
                except APIError as e:
                    LOG.warning("cannot create network for %s (%s): %s; using shared network",
                                subnet_id, cidr, e.explanation)
            name = f"{config.PREFIX}-shared"
            try:
                return self.client.networks.get(name), False
            except NotFound:
                return self.client.networks.create(
                    name, driver="bridge", labels={_label("shared"): "true"}
                ), False

    # -------------------------------------------------------------- instances
    def run_instance(
        self,
        *,
        instance_id: str,
        account_id: str,
        region: str,
        image_ref: str,
        subnet_id: str | None,
        subnet_cidr: str | None,
        private_ip: str | None,
        ssh_pubkey: str | None,
        user_data_b64: str | None,
        instance_type: str,
    ) -> str:
        """Start the container for an instance; returns its IP (inside the subnet when possible)."""
        network, matches = self._network_for_subnet(subnet_id, subnet_cidr)
        ip: str | None = None
        if matches and subnet_cidr:
            ip = private_ip if self._usable_ip(private_ip, subnet_cidr) and \
                private_ip not in self._used_ips(network) else self._allocate_ip(network, subnet_cidr)
        hostname = "ip-" + ip.replace(".", "-") if ip else f"{config.PREFIX}-{instance_id}"
        env = {
            "EC2SIM_INSTANCE_ID": instance_id,
            "EC2SIM_REGION": region,
            "EC2SIM_INSTANCE_TYPE": instance_type,
            "EC2SIM_SSH_PUBKEY": ssh_pubkey or "",
            "EC2SIM_USER_DATA": user_data_b64 or "",
        }
        run_opts: dict[str, Any] = {"init": True}
        if self._is_systemd_image(image_ref):
            # systemd must be PID 1: privileged, own cgroup namespace, tmpfs /run.
            run_opts = {
                "init": False,
                "privileged": True,
                "cgroupns": "private",
                "tmpfs": {"/run": "rw,exec", "/run/lock": "rw"},
            }
        run_opts.update(json.loads(config.DOCKER_RUN_EXTRA) if config.DOCKER_RUN_EXTRA else {})
        container = self.client.containers.create(
            image_ref,
            name=f"{config.PREFIX}-{instance_id}",
            hostname=hostname,
            environment=env,
            labels={
                _label("instance_id"): instance_id,
                _label("account"): account_id,
                _label("region"): region,
            },
            detach=True,
            network=None,
            **run_opts,
        )
        # Swap Docker's default bridge for the subnet network, at moto's private IP when it fits.
        try:
            self.client.api.disconnect_container_from_network(container.id, "bridge")
        except APIError:
            pass
        ip_ok = False
        if ip:
            try:
                network.connect(container, ipv4_address=ip)
                ip_ok = True
            except APIError as e:
                LOG.info("ip %s unavailable on %s: %s", ip, network.name, e.explanation)
        if not ip_ok:
            network.connect(container)
        if self.imds_network is not None:
            self.imds_network.connect(container)
        container.start()
        return self.container_ip(instance_id) or ""

    def _is_systemd_image(self, image_ref: str) -> bool:
        labels = self.client.images.get(image_ref).labels or {}
        return labels.get(_label("systemd")) == "true"

    # -------------------------------------------------------------------- IMDS
    IMDS_NET = f"{config.PREFIX}-imds"
    IMDS_PROXY = f"{config.PREFIX}-imds-proxy"

    def ensure_imds(self, port: int) -> bool:
        """Create the link-local network and a proxy container at 169.254.169.254
        that forwards to ec2-sim's IMDS server on the network gateway, adding
        X-Forwarded-For so the server knows which instance is asking."""
        try:
            try:
                self.imds_network = self.client.networks.get(self.IMDS_NET)
            except NotFound:
                ipam = docker.types.IPAMConfig(pool_configs=[docker.types.IPAMPool(
                    subnet=config.IMDS_SUBNET, gateway=config.IMDS_GATEWAY)])
                self.imds_network = self.client.networks.create(
                    self.IMDS_NET, driver="bridge", ipam=ipam, labels={_label("imds"): "true"})
            try:
                self.client.containers.get(self.IMDS_PROXY).remove(force=True)
            except NotFound:
                pass
            conf = (
                "server { listen 80; location / { "
                f"proxy_pass http://{config.IMDS_GATEWAY}:{port}; "
                "proxy_set_header X-Forwarded-For $remote_addr; proxy_set_header Host $host; } }"
            )
            try:
                self.client.images.get(config.IMDS_PROXY_IMAGE)
            except ImageNotFound:
                self.client.images.pull(config.IMDS_PROXY_IMAGE)
            proxy = self.client.containers.create(
                config.IMDS_PROXY_IMAGE,
                name=self.IMDS_PROXY,
                command=["sh", "-c", f"echo '{conf}' > /etc/nginx/conf.d/default.conf "
                                     "&& exec nginx -g 'daemon off;'"],
                labels={_label("imds"): "true"},
                detach=True,
                network=None,
            )
            try:
                self.client.api.disconnect_container_from_network(proxy.id, "bridge")
            except APIError:
                pass
            self.imds_network.connect(proxy, ipv4_address=config.IMDS_ADDRESS)
            proxy.start()
            return True
        except Exception as e:  # noqa: BLE001
            LOG.warning("IMDS disabled: %s", e)
            self.imds_network = None
            return False

    def instance_for_imds_ip(self, ip: str) -> dict[str, str] | None:
        """Map a caller's IMDS-network IP back to its instance labels."""
        if self.imds_network is None:
            return None
        self.imds_network.reload()
        for cid, ep in self.imds_network.attrs.get("Containers", {}).items():
            if ep.get("IPv4Address", "").split("/")[0] == ip:
                labels = self.client.containers.get(cid).labels
                if labels.get(_label("instance_id")):
                    return {"instance_id": labels[_label("instance_id")],
                            "account": labels.get(_label("account"), ""),
                            "region": labels.get(_label("region"), "")}
        return None

    @staticmethod
    def _usable_ip(ip: str | None, cidr: str | None) -> bool:
        """AWS reserves the first four addresses and the last one of every subnet
        (.1 is also Docker's gateway), so instances never get those."""
        if not ip or not cidr:
            return False
        net = ipaddress.ip_network(cidr, strict=False)
        addr = ipaddress.ip_address(ip)
        return addr in net and net.network_address + 4 <= addr < net.broadcast_address

    def _used_ips(self, network) -> set[str]:
        network.reload()
        return {c["IPv4Address"].split("/")[0] for c in network.attrs.get("Containers", {}).values()}

    def _allocate_ip(self, network, cidr: str) -> str | None:
        used = self._used_ips(network)
        net = ipaddress.ip_network(cidr, strict=False)
        for addr in net.hosts():
            if self._usable_ip(str(addr), cidr) and str(addr) not in used:
                return str(addr)
        return None

    def _container(self, instance_id: str):
        try:
            return self.client.containers.get(f"{config.PREFIX}-{instance_id}")
        except NotFound:
            return None

    def container_ip(self, instance_id: str) -> str | None:
        c = self._container(instance_id)
        if not c:
            return None
        c.reload()
        for name, net in c.attrs["NetworkSettings"]["Networks"].items():
            if name != self.IMDS_NET and net.get("IPAddress"):
                return net["IPAddress"]
        return None

    def stop_instance(self, instance_id: str) -> None:
        if c := self._container(instance_id):
            c.stop(timeout=config.STOP_TIMEOUT)

    def start_instance(self, instance_id: str) -> str | None:
        if c := self._container(instance_id):
            c.start()
        return self.container_ip(instance_id)

    def reboot_instance(self, instance_id: str) -> None:
        if c := self._container(instance_id):
            c.restart(timeout=config.STOP_TIMEOUT)

    def terminate_instance(self, instance_id: str) -> None:
        if c := self._container(instance_id):
            if config.REMOVE_CONTAINERS:
                c.remove(force=True)
            else:
                c.stop(timeout=config.STOP_TIMEOUT)

    def has_instance(self, instance_id: str) -> bool:
        return self._container(instance_id) is not None

    # ------------------------------------------------------------------- AMIs
    def commit_instance(self, instance_id: str, ami_id: str, labels: dict[str, str]) -> str:
        """CreateImage: snapshot the instance's filesystem into ec2sim/ami:<ami-id>."""
        c = self._container(instance_id)
        if c is None:
            raise RuntimeError(f"no container for {instance_id}")
        all_labels = {_label(k): v for k, v in labels.items()}
        all_labels[_label("ami_id")] = ami_id
        # Drop the source instance's labels so the image isn't mistaken for an instance.
        for key in ("instance_id",):
            all_labels.setdefault(_label(key), "")
        c.commit(
            repository=config.AMI_REPO,
            tag=ami_id,
            message=f"CreateImage({instance_id}) -> {ami_id}",
            conf={"Labels": all_labels},
        )
        return f"{config.AMI_REPO}:{ami_id}"

    def register_docker_image(self, source_ref: str, ami_id: str, labels: dict[str, str]) -> str:
        """RegisterImage with ImageLocation=docker://<ref>: wrap an existing image as an AMI."""
        try:
            self.client.images.get(source_ref)
        except ImageNotFound:
            LOG.info("pulling %s", source_ref)
            self.client.images.pull(source_ref)
        lines = [f"FROM {source_ref}"]
        all_labels = {**{_label(k): v for k, v in labels.items()}, _label("ami_id"): ami_id}
        lines += [f"LABEL {json.dumps(k)}={json.dumps(v)}" for k, v in all_labels.items()]
        ref = f"{config.AMI_REPO}:{ami_id}"
        self.client.images.build(fileobj=io.BytesIO("\n".join(lines).encode()), tag=ref, rm=True)
        return ref

    def remove_ami_image(self, ami_id: str) -> None:
        try:
            self.client.images.remove(f"{config.AMI_REPO}:{ami_id}", noprune=False)
        except (ImageNotFound, APIError) as e:
            LOG.info("could not remove image for %s: %s", ami_id, e)

    def list_ami_images(self, account_id: str, region: str) -> list[dict[str, str]]:
        """Committed/registered AMIs for this account+region, from image labels."""
        out = []
        for img in self.client.images.list(filters={"label": _label("ami_id")}):
            labels = img.labels or {}
            if labels.get(_label("account")) != account_id or labels.get(_label("region")) != region:
                continue
            meta = {k[len(config.LABEL) + 1:]: v for k, v in labels.items()
                    if k.startswith(config.LABEL + ".")}
            meta["created"] = img.attrs.get("Created", "")
            out.append(meta)
        return out

    # -------------------------------------------------------------- lifecycle
    def cleanup(self) -> None:
        """Remove instance containers and subnet networks (AMI images are kept)."""
        if not config.REMOVE_CONTAINERS:
            return
        for c in self.client.containers.list(all=True, filters={"label": _label("instance_id")}):
            if c.labels.get(_label("instance_id")):
                c.remove(force=True)
        for n in self.client.networks.list(filters={"label": _label("subnet_id")}):
            try:
                n.remove()
            except APIError:
                pass
        for c in self.client.containers.list(all=True, filters={"label": _label("imds")}):
            c.remove(force=True)
        for n in self.client.networks.list(filters={"label": _label("imds")}) + \
                self.client.networks.list(filters={"label": _label("shared")}):
            try:
                n.remove()
            except APIError:
                pass
