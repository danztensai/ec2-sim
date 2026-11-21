"""Docker-backed VM manager: instances are containers, AMIs are images.

Docker objects carry `ec2sim.*` labels so state can be rediscovered:
  instance containers: ec2sim.instance_id / account / region
  AMI images (ec2sim/ami:<ami-id>): ec2sim.ami_id / account / region / name / ...
"""

from __future__ import annotations

import io
import ipaddress
import json
import tarfile
import logging
import threading
from typing import Any

import docker
from docker.errors import APIError, ImageNotFound, NotFound

from . import config
from .catalog import BASE_AMIS, BaseAmi

LOG = logging.getLogger("ec2sim.vm")


# Units that must never run inside an instance container (they act on the host).
SYSTEMD_MASKED_UNITS = (
    "systemd-binfmt.service",
    "proc-sys-fs-binfmt_misc.automount",
    "proc-sys-fs-binfmt_misc.mount",
)


def _label(key: str) -> str:
    return f"{config.LABEL}.{key}"


class DockerVMManager:
    def __init__(self) -> None:
        self.client = docker.from_env()
        self.client.ping()
        # Commits/builds of large instances can take minutes: no 60 s client timeout.
        self.slow_client = docker.from_env(timeout=None)
        self._build_lock = threading.Lock()
        self._net_lock = threading.Lock()
        self._built: set[str] = set()
        self.imds_network = None
        self.info = self.client.info()
        # EC2 architecture of the images this engine builds and runs.
        self.arch = "arm64" if self.info.get("Architecture") in ("aarch64", "arm64") else "x86_64"
        self.network_mode = self._detect_network_mode()
        self.bastion = None

    def _detect_network_mode(self) -> str:
        if config.NETWORK_MODE in ("routed", "bastion"):
            LOG.info("network mode: %s (EC2SIM_NETWORK_MODE)", config.NETWORK_MODE)
            return config.NETWORK_MODE
        os_name = self.info.get("OperatingSystem", "")
        # Docker Desktop (macOS/Windows) runs the engine in a VM whose bridge
        # networks aren't routable from the host. OrbStack and native Linux/WSL2
        # engines route container IPs to the host.
        if "Docker Desktop" in os_name:
            mode = "bastion"
        else:
            mode = "routed"
        LOG.info("network mode: %s (engine: %s)", mode, os_name or "unknown")
        return mode

    # ---------------------------------------------------------------- bastion
    BASTION = f"{config.PREFIX}-bastion"

    def ensure_bastion(self) -> None:
        """SSH jump host on 127.0.0.1:BASTION_PORT for hosts that can't reach
        instance IPs directly. Joined to every instance network on demand."""
        ref = f"{config.PREFIX}/bastion:latest"
        try:
            self.client.images.get(ref)
        except ImageNotFound:
            LOG.info("building %s", ref)
            self.client.images.build(path=str(config.IMAGES_DIR),
                                     dockerfile="bastion/Dockerfile", tag=ref, rm=True)
        try:
            self.client.containers.get(self.BASTION).remove(force=True)
        except NotFound:
            pass
        self.bastion = self.client.containers.run(
            ref,
            name=self.BASTION,
            detach=True,
            labels={_label("bastion"): "true"},
            environment={"EC2SIM_BASTION_PUBKEY": config.BASTION_PUBKEY},
            ports={"22/tcp": ("127.0.0.1", config.BASTION_PORT)},
        )
        LOG.info("SSH bastion on 127.0.0.1:%s (user ec2sim, password ec2sim): "
                 "ssh -J ec2sim@127.0.0.1:%s <user>@<instance-ip>",
                 config.BASTION_PORT, config.BASTION_PORT)

    def _attach_bastion(self, network) -> None:
        """Join helper containers (SSH bastion, HTTP gateway) to an instance network."""
        network.reload()
        attached = network.attrs.get("Containers", {})
        for helper in (self.bastion, self._gateway()):
            if helper is not None and helper.id not in attached:
                try:
                    network.connect(helper)
                except APIError as e:
                    LOG.warning("could not attach %s to %s: %s", helper.name, network.name, e.explanation)

    # ---------------------------------------------------------------- gateway
    GATEWAY = f"{config.PREFIX}-gateway"

    def _gateway(self):
        try:
            return self.client.containers.get(self.GATEWAY)
        except NotFound:
            return None

    # Ports served as TLS passthrough (routed by SNI, the instance terminates TLS).
    TLS_PORTS = {443}

    @staticmethod
    def gateway_conf(ports: list[int], allowed: set[str] | None) -> str:
        """Full nginx.conf for the gateway.

        HTTP ports: <name>.localhost:P -> <name>:P (Host header).
        TLS ports (443): SNI <name>.localhost -> <name>:443, passed through untouched.
        Names resolve via Docker DNS (instance Name tag / instance ID on the VPC
        networks). `allowed` = {"name:port", ...}; None = open to every instance."""
        def q(x: str) -> str:
            return '"' + x.replace('"', "") + '"'

        http_ports = [p for p in ports if p not in DockerVMManager.TLS_PORTS]
        tls_ports = [p for p in ports if p in DockerVMManager.TLS_PORTS]
        entries = " ".join(f"{q(a)} 1;" for a in sorted(allowed or ()))
        default = "1" if allowed is None else "0"
        servers = []
        for p in http_ports:
            servers.append(
                f"server {{ listen {p}; server_name ~^(?<inst>[a-z0-9][a-z0-9-]*)\\.localhost$; "
                "location / { default_type text/plain; if ($ec2sim_allowed = 0) { "
                f"return 403 'ec2-sim gateway: port {p} on \"$inst\" is not open. Allow inbound TCP {p} "
                "in one of its security groups (or tag the instance ec2sim:web), or check the name.\\n'; } "
                f"proxy_pass http://$inst:{p}; proxy_http_version 1.1; "
                "proxy_set_header Host $host; proxy_set_header X-Forwarded-For $remote_addr; "
                "proxy_set_header X-Forwarded-Proto $scheme; proxy_set_header Upgrade $http_upgrade; "
                "proxy_set_header Connection $connection_upgrade; proxy_read_timeout 3600s; "
                "proxy_intercept_errors off; error_page 502 504 = @noinst; } "
                f"location @noinst {{ default_type text/plain; return 502 'ec2-sim gateway: nothing answering at "
                f"instance \"$inst\" port {p} (is it running and listening on 0.0.0.0?)\\n'; }} }} "
                f"server {{ listen {p} default_server; default_type text/plain; "
                f"return 404 'ec2-sim gateway: use http://<instance-name>.localhost:{p}/ "
                "(instance Name tag or instance ID)\\n'; }"
            )
        http = (
            "http { access_log off; resolver 127.0.0.11 valid=5s ipv6=off; "
            "map $http_upgrade $connection_upgrade { default upgrade; '' close; } "
            f"map \"$inst:$server_port\" $ec2sim_allowed {{ default {default}; {entries} }} "
            + " ".join(servers) + " }"
        )
        stream = ""
        if tls_ports:
            tls_servers = []
            for p in tls_ports:
                if allowed is None:
                    backend = f"map $ssl_preread_server_name $ec2sim_tls_{p} {{ ~^(?<n>[a-z0-9][a-z0-9-]*)\\.localhost$ $n:{p}; default ''; }}"
                else:
                    names = sorted(a.rsplit(":", 1)[0] for a in (allowed or ()) if a.endswith(f":{p}"))
                    pairs = " ".join(f"{q(n + '.localhost')} {n}:{p};" for n in names)
                    backend = f"map $ssl_preread_server_name $ec2sim_tls_{p} {{ default ''; {pairs} }}"
                tls_servers.append(f"{backend} server {{ listen {p}; ssl_preread on; proxy_pass $ec2sim_tls_{p}; "
                                   "proxy_connect_timeout 5s; }")
            stream = "stream { resolver 127.0.0.11 valid=5s ipv6=off; " + " ".join(tls_servers) + " }"
        return ("worker_processes 1; error_log /dev/stderr warn; pid /tmp/nginx.pid; "
                "events { worker_connections 1024; } " + http + " " + stream)

    def update_gateway_access(self, allowed: set[str] | None) -> bool:
        """Rewrite the gateway's allow-map and reload nginx if it changed."""
        g = self._gateway()
        if g is None:
            return False
        key = (g.id, None if allowed is None else frozenset(allowed))
        if getattr(self, "_gw_access_key", None) == key:
            return False
        conf = self.gateway_conf(self.gateway_ports(), allowed)
        res = g.exec_run(["sh", "-c", "printf '%s' \"$GW_CONF\" > /etc/nginx/nginx.conf && nginx -s reload"],
                         environment={"GW_CONF": conf})
        if res.exit_code != 0:
            LOG.warning("gateway reload failed: %s", res.output.decode(errors="replace").strip())
            return False
        self._gw_access_key = key
        return True

    def ensure_gateway(self, ports: list[int]) -> list[int]:
        """(Re)create the gateway publishing `ports` on GATEWAY_BIND; returns the ports
        actually published (ports already in use on the host are skipped)."""
        mode = config.GATEWAY_ACCESS
        if (g := self._gateway()) is not None:
            # Keep the mode the server chose (CLI calls may run with other env).
            mode = g.labels.get(_label("gateway_access")) or mode
            g.remove(force=True)
        ports = sorted(set(p for p in ports if 0 < p < 65536))
        if not ports:
            return []
        try:
            self.client.images.get(config.IMDS_PROXY_IMAGE)
        except ImageNotFound:
            self.slow_client.images.pull(config.IMDS_PROXY_IMAGE)
        published = list(ports)
        while published:
            try:
                g = self.client.containers.run(
                    config.IMDS_PROXY_IMAGE,
                    ["sh", "-c", "printf '%s' \"$GW_CONF\" > /etc/nginx/nginx.conf && exec nginx -g 'daemon off;'"],
                    name=self.GATEWAY,
                    detach=True,
                    environment={"GW_CONF": self.gateway_conf(published, None if mode == "open" else set())},
                    ports={f"{p}/tcp": (config.GATEWAY_BIND, p) for p in published},
                    labels={_label("gateway"): "true", _label("gateway_ports"): ",".join(map(str, published)),
                            _label("gateway_access"): mode},
                )
                break
            except APIError as e:
                # A port in use on the host: drop it and retry with the rest.
                bad = next((p for p in published if f":{p}" in str(e.explanation)), None)
                try:
                    self.client.containers.get(self.GATEWAY).remove(force=True)
                except NotFound:
                    pass
                if bad is None:
                    LOG.warning("HTTP gateway disabled: %s", e.explanation)
                    return []
                LOG.warning("gateway: host port %s is in use, skipping it", bad)
                published.remove(bad)
        else:
            return []
        # Join every existing instance network.
        for n in self.client.networks.list(filters={"label": _label("vpc_id")}) + \
                self.client.networks.list(filters={"label": _label("shared")}):
            try:
                n.connect(g)
            except APIError:
                pass
        LOG.info("HTTP gateway: http://<instance-name>.localhost%s on %s",
                 "" if published == [80] else ":" + "/".join(map(str, published)), config.GATEWAY_BIND)
        return published

    def gateway_ports(self) -> list[int]:
        g = self._gateway()
        if g is None:
            return []
        return [int(p) for p in (g.labels.get(_label("gateway_ports")) or "").split(",") if p]

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
    def _network_for_vpc(self, vpc_id: str | None, vpc_cidr: str | None) -> tuple[Any, bool]:
        """One bridge network per VPC (its CIDR), so instances in any of its subnets
        reach each other, as in AWS. Returns (network, cidr_matches); falls back to
        a shared network if the CIDR overlaps an existing Docker network."""
        with self._net_lock:
            if vpc_id and vpc_cidr:
                name = f"{config.PREFIX}-{vpc_id}"
                try:
                    return self.client.networks.get(name), True
                except NotFound:
                    pass
                try:
                    ipam = docker.types.IPAMConfig(
                        pool_configs=[docker.types.IPAMPool(subnet=vpc_cidr)]
                    )
                    net = self.client.networks.create(
                        name,
                        driver="bridge",
                        ipam=ipam,
                        labels={_label("vpc_id"): vpc_id},
                    )
                    return net, True
                except APIError as e:
                    LOG.warning("cannot create network for %s (%s): %s; using shared network",
                                vpc_id, vpc_cidr, e.explanation)
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
        vpc_id: str | None,
        vpc_cidr: str | None,
        private_ip: str | None,
        ssh_pubkey: str | None,
        user_data_b64: str | None,
        instance_type: str,
        aliases: list[str] | None = None,
    ) -> str:
        """Start the container for an instance; returns its IP (inside the subnet when possible).

        `aliases` become DNS names for the instance on its VPC network."""
        network, matches = self._network_for_vpc(vpc_id, vpc_cidr)
        self._attach_bastion(network)
        ip: str | None = None
        in_vpc = bool(subnet_cidr and vpc_cidr) and ipaddress.ip_network(subnet_cidr, strict=False).subnet_of(
            ipaddress.ip_network(vpc_cidr, strict=False))
        if matches and in_vpc:
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
                # /proc/sys/fs/binfmt_misc is kernel-global: a privileged container's
                # systemd-binfmt would wipe the HOST's registrations (e.g. WSL interop,
                # qemu-user). Mask it, also for AMIs baked before images masked it.
                "mounts": [
                    docker.types.Mount(f"/etc/systemd/system/{unit}", "/dev/null", type="bind", read_only=True)
                    for unit in SYSTEMD_MASKED_UNITS
                ],
            }
        extra_hosts = self.image_extra_hosts(image_ref)
        if extra_hosts:
            run_opts["extra_hosts"] = extra_hosts
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
        private_dns = []
        if ip:
            # AWS private DNS: ip-a-b-c-d.ec2.internal (us-east-1) / .<region>.compute.internal
            suffix = "ec2.internal" if region == "us-east-1" else f"{region}.compute.internal"
            private_dns = [f"{hostname}.{suffix}"]
        dns = sorted({a for a in ([hostname] + private_dns + (aliases or [])) if a})
        if ip:
            try:
                network.connect(container, ipv4_address=ip, aliases=dns)
                ip_ok = True
            except APIError as e:
                LOG.info("ip %s unavailable on %s: %s", ip, network.name, e.explanation)
        if not ip_ok:
            network.connect(container, aliases=dns)
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
            upstream = config.IMDS_GATEWAY if self.network_mode == "routed" else "host.docker.internal"
            conf = (
                "server { listen 80; location / { "
                f"proxy_pass http://{upstream}:{port}; "
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
                extra_hosts={"host.docker.internal": "host-gateway"},
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
        self.remove_forwards(instance_id=instance_id)
        if c := self._container(instance_id):
            if config.REMOVE_CONTAINERS:
                c.remove(force=True)
            else:
                c.stop(timeout=config.STOP_TIMEOUT)

    def has_instance(self, instance_id: str) -> bool:
        return self._container(instance_id) is not None

    # ------------------------------------------------------------------- AMIs
    # Docker manages /etc/hosts per container (bind mount), so `docker commit`
    # drops entries a provisioner added. Real AMIs keep them: carry them in a label.
    _HOSTS_DEFAULT_NAMES = {"localhost", "ip6-localhost", "ip6-loopback", "ip6-localnet",
                            "ip6-mcastprefix", "ip6-allnodes", "ip6-allrouters"}

    def _baked_hosts(self, c) -> list[list]:
        """Extra /etc/hosts entries of a container: [[ip, [names...]], ...]."""
        try:
            stream, _ = c.get_archive("/etc/hosts")
            data = b"".join(stream)
            with tarfile.open(fileobj=io.BytesIO(data)) as tar:
                member = tar.next()
                text = tar.extractfile(member).read().decode(errors="replace") if member else ""
        except Exception as e:  # noqa: BLE001
            LOG.info("could not read /etc/hosts of %s: %s", c.name, e)
            return []
        own = {net.get("IPAddress") for net in (c.attrs.get("NetworkSettings", {}).get("Networks") or {}).values()}
        return self.parse_extra_hosts(text, own)

    @classmethod
    def parse_extra_hosts(cls, text: str, own_ips: set) -> list[list]:
        """Entries a provisioner added to /etc/hosts (not Docker's defaults or the
        container's own address): [[ip, [names...]], ...]."""
        out: dict[str, list[str]] = {}
        for line in text.splitlines():
            parts = line.split("#", 1)[0].split()
            if len(parts) < 2 or parts[0] in own_ips:
                continue
            names = [n for n in parts[1:] if n not in cls._HOSTS_DEFAULT_NAMES]
            if names:
                out.setdefault(parts[0], [])
                out[parts[0]] += [n for n in names if n not in out[parts[0]]]
        return [[ip, names] for ip, names in out.items()]

    def image_extra_hosts(self, image_ref: str) -> dict[str, str]:
        """name -> ip for /etc/hosts entries baked into an AMI image."""
        try:
            raw = (self.client.images.get(image_ref).labels or {}).get(_label("etc_hosts"))
            return {name: ip for ip, names in json.loads(raw or "[]") for name in names}
        except Exception:  # noqa: BLE001
            return {}

    def commit_instance(self, instance_id: str, ami_id: str, labels: dict[str, str]) -> str:
        """CreateImage: snapshot the instance's filesystem into ec2sim/ami:<ami-id>."""
        c = self._container(instance_id)
        if c is None:
            raise RuntimeError(f"no container for {instance_id}")
        all_labels = {_label(k): v for k, v in labels.items()}
        all_labels[_label("ami_id")] = ami_id
        c.reload()
        hosts = self._baked_hosts(c)
        if hosts:
            all_labels[_label("etc_hosts")] = json.dumps(hosts)
        # Drop the source instance's labels so the image isn't mistaken for an instance.
        for key in ("instance_id",):
            all_labels.setdefault(_label(key), "")
        self.slow_client.api.commit(
            c.id,
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
            self.slow_client.images.pull(source_ref)
        lines = [f"FROM {source_ref}"]
        all_labels = {**{_label(k): v for k, v in labels.items()}, _label("ami_id"): ami_id}
        lines += [f"LABEL {json.dumps(k)}={json.dumps(v)}" for k, v in all_labels.items()]
        ref = f"{config.AMI_REPO}:{ami_id}"
        self.slow_client.images.build(fileobj=io.BytesIO("\n".join(lines).encode()), tag=ref, rm=True)
        return ref

    def remove_ami_image(self, ami_id: str) -> None:
        """DeregisterImage. Like AWS, allowed while instances from it still run:
        the tag goes now (force), Docker frees the layers when the last user exits."""
        try:
            self.client.images.remove(f"{config.AMI_REPO}:{ami_id}", force=True, noprune=False)
        except (ImageNotFound, APIError) as e:
            LOG.info("could not remove image for %s: %s", ami_id, e)

    def list_ami_images(self, account_id: str, region: str) -> list[dict[str, str]]:
        """Committed/registered AMIs for this account+region, from image labels."""
        out = []
        for img in self.client.images.list(filters={"label": _label("ami_id")}):
            labels = img.labels or {}
            if labels.get(_label("account")) != account_id or labels.get(_label("region")) != region:
                continue
            # Deregistered AMIs lose their tag (layers may linger while in use): skip them.
            if f"{config.AMI_REPO}:{labels.get(_label('ami_id'))}" not in (img.tags or []):
                continue
            meta = {k[len(config.LABEL) + 1:]: v for k, v in labels.items()
                    if k.startswith(config.LABEL + ".")}
            meta["created"] = img.attrs.get("Created", "")
            out.append(meta)
        return out

    # ------------------------------------------------------------ port forwards
    FORWARDER_IMAGE = f"{config.PREFIX}/forwarder:latest"

    def _instance_network_ip(self, instance_id: str) -> tuple[Any, str]:
        c = self._container(instance_id)
        if c is None:
            raise RuntimeError(f"no running container for {instance_id}")
        c.reload()
        for name, net in c.attrs["NetworkSettings"]["Networks"].items():
            if name != self.IMDS_NET and net.get("IPAddress"):
                return self.client.networks.get(name), net["IPAddress"]
        raise RuntimeError(f"{instance_id} has no network address (is it running?)")

    def add_forward(self, instance_id: str, remote_port: int, local_port: int, bind: str = "127.0.0.1") -> dict:
        """Publish bind:local_port on the host, forwarded to instance:remote_port.
        Works on any host OS (Docker publishes to the host's localhost; on WSL that
        is also reachable from Windows, on Docker Desktop from macOS/Windows)."""
        network, ip = self._instance_network_ip(instance_id)
        try:
            self.client.images.get(self.FORWARDER_IMAGE)
        except ImageNotFound:
            self.slow_client.images.build(path=str(config.IMAGES_DIR), dockerfile="forwarder/Dockerfile",
                                          tag=self.FORWARDER_IMAGE, rm=True)
        name = f"{config.PREFIX}-fwd-{instance_id}-{remote_port}-{local_port}"
        try:
            self.client.containers.get(name).remove(force=True)
        except NotFound:
            pass
        try:
            self.client.containers.run(
                self.FORWARDER_IMAGE,
                [f"TCP-LISTEN:{remote_port},fork,reuseaddr", f"TCP:{ip}:{remote_port}"],
                name=name,
                detach=True,
                network=network.name,
                ports={f"{remote_port}/tcp": (bind, local_port)},
                restart_policy={"Name": "unless-stopped"},
                labels={
                    _label("forward"): "true",
                    _label("fwd_instance"): instance_id,
                    _label("fwd_remote"): str(remote_port),
                    _label("fwd_local"): str(local_port),
                    _label("fwd_bind"): bind,
                },
            )
        except APIError as e:
            raise RuntimeError(f"cannot forward {bind}:{local_port}: {e.explanation}") from e
        return {"name": name, "instance_id": instance_id, "remote_port": remote_port,
                "local_port": local_port, "bind": bind, "target": f"{ip}:{remote_port}"}

    def list_forwards(self) -> list[dict]:
        out = []
        for c in self.client.containers.list(all=True, filters={"label": _label("forward")}):
            lb = c.labels
            out.append({
                "name": c.name,
                "instance_id": lb.get(_label("fwd_instance"), ""),
                "remote_port": int(lb.get(_label("fwd_remote"), "0")),
                "local_port": int(lb.get(_label("fwd_local"), "0")),
                "bind": lb.get(_label("fwd_bind"), "127.0.0.1"),
                "status": c.status,
            })
        return sorted(out, key=lambda f: (f["instance_id"], f["remote_port"]))

    def remove_forwards(self, name: str | None = None, instance_id: str | None = None) -> int:
        n = 0
        for c in self.client.containers.list(all=True, filters={"label": _label("forward")}):
            if (name and c.name != name) or (instance_id and c.labels.get(_label("fwd_instance")) != instance_id):
                continue
            c.remove(force=True)
            n += 1
        return n

    # -------------------------------------------------------------- lifecycle
    def cleanup(self) -> None:
        """Remove instance containers and subnet networks (AMI images are kept)."""
        if not config.REMOVE_CONTAINERS:
            return
        # Containers first (instances, IMDS proxy, bastion), then the networks they used.
        for c in self.client.containers.list(all=True, filters={"label": _label("instance_id")}):
            if c.labels.get(_label("instance_id")):
                c.remove(force=True)
        for key in ("imds", "bastion", "forward", "gateway"):
            for c in self.client.containers.list(all=True, filters={"label": _label(key)}):
                c.remove(force=True)
        self.bastion = None
        for n in self.client.networks.list(filters={"label": _label("vpc_id")}) + \
                self.client.networks.list(filters={"label": _label("subnet_id")}) + \
                self.client.networks.list(filters={"label": _label("imds")}) + \
                self.client.networks.list(filters={"label": _label("shared")}):
            try:
                n.remove()
            except APIError:
                pass
