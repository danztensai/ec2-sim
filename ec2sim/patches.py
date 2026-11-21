"""Hook moto's EC2 models so API calls also drive real Docker resources.

moto keeps owning the API surface (request parsing, validation, XML responses,
Describe* filters, VPC/SG/key-pair bookkeeping). We wrap only the model
methods where something real should happen.
"""

from __future__ import annotations

import base64
import ipaddress
import threading
import time
import logging
from datetime import datetime
from typing import Any

from moto.ec2.models.amis import Ami, AmiBackend
from moto.ec2.models.instances import Instance, InstanceBackend
from moto.ec2.responses.amis import AmisResponse

from . import config
from .catalog import BASE_AMIS, BASE_BY_ID, BASE_CREATION_DATE
from .vm import DockerVMManager

LOG = logging.getLogger("ec2sim.patches")

VM: DockerVMManager | None = None


def _wrap(cls: type, name: str):
    """Decorator: replace cls.name with fn(original, self, *args, **kwargs)."""
    original = getattr(cls, name)

    def decorator(fn):
        def wrapper(self, *args, **kwargs):
            return fn(original, self, *args, **kwargs)

        wrapper.__name__ = name
        wrapper.__wrapped__ = original
        setattr(cls, name, wrapper)
        return wrapper

    return decorator


def _user_data_b64(user_data: Any) -> str | None:
    if not user_data:
        return None
    if isinstance(user_data, (list, tuple)):
        user_data = user_data[0] if user_data else None
        if not user_data:
            return None
    if hasattr(user_data, "as_bytes"):  # moto.core.types.Base64EncodedString
        user_data = user_data.as_bytes()
    if isinstance(user_data, bytes):
        user_data = user_data.decode()
    # moto keeps what the API received, which is already base64.
    try:
        base64.b64decode(user_data, validate=True)
        return user_data
    except Exception:  # noqa: BLE001
        return base64.b64encode(user_data.encode()).decode()


def _arch() -> str:
    """EC2 architecture matching the Docker engine (arm64 on Apple Silicon)."""
    if VM is not None:
        return VM.arch
    import platform

    return "arm64" if platform.machine() in ("arm64", "aarch64") else "x86_64"


def _set_public_ip(instance: Instance, ip: str | None) -> None:
    if ip and instance.nics:
        instance.nics[0].public_ip = ip


def apply(vm: DockerVMManager | None) -> None:
    global VM
    VM = vm

    # ------------------------------------------------------------------ AMIs
    @_wrap(AmiBackend, "_load_amis")
    def _load_amis(original, self: AmiBackend):
        original(self)
        for base in BASE_AMIS:
            ami = Ami(
                self,
                base.ami_id,
                name=base.name,
                description=base.description,
                owner_id=base.owner_id,
                owner_alias=base.owner_alias or None,
                public=True,
                virtualization_type="hvm",
                architecture=_arch(),
                creation_date=BASE_CREATION_DATE,
                image_location=f"docker:{base.docker_image}",
                hypervisor="xen",
                root_device_type="ebs",
                region_name=self.region_name,  # type: ignore[attr-defined]
                boot_mode="uefi-preferred",
            )
            ami.ec2sim_image = base.docker_image  # type: ignore[attr-defined]
            self.amis[base.ami_id] = ami
        if VM is None:
            return
        # Re-register AMIs committed in earlier runs (they live on as labelled images).
        try:
            found = VM.list_ami_images(self.account_id, self.region_name)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            LOG.exception("could not list ec2-sim AMI images")
            return
        for meta in found:
            ami_id = meta["ami_id"]
            created = None
            if meta.get("created"):
                try:
                    created = datetime.fromisoformat(meta["created"][:26].rstrip("Z") + "+00:00")
                except ValueError:
                    created = None
            ami = Ami(
                self,
                ami_id,
                name=meta.get("name") or ami_id,
                description=meta.get("description", ""),
                owner_id=self.account_id,  # type: ignore[attr-defined]
                virtualization_type="hvm",
                architecture=meta.get("architecture") or _arch(),
                creation_date=created,
                image_location=f"docker:{config.AMI_REPO}:{ami_id}",
                root_device_type="ebs",
                region_name=self.region_name,  # type: ignore[attr-defined]
            )
            ami.ec2sim_image = f"{config.AMI_REPO}:{ami_id}"  # type: ignore[attr-defined]
            self.amis[ami_id] = ami
            LOG.info("restored AMI %s (%s) from Docker", ami_id, ami.name)

    def _ami_labels(backend: Any, ami: Ami, **extra: str) -> dict[str, str]:
        labels = {
            "account": backend.account_id,
            "region": backend.region_name,
            "name": ami.name or "",
            "description": ami.description or "",
            "architecture": ami.architecture or _arch(),
        }
        labels.update({k: v for k, v in extra.items() if v is not None})
        return labels

    @_wrap(AmiBackend, "create_image")
    def create_image(original, self: AmiBackend, instance_id: str, name: str,
                     description: str, tag_specifications: list[dict[str, Any]]):
        ami = original(self, instance_id, name, description, tag_specifications)
        ami.root_device_type = "ebs"
        ami.architecture = _arch()
        if VM is not None and VM.has_instance(instance_id):
            # Like AWS: return at once as "pending"; snapshot in the background.
            # Large instances take minutes to commit; clients poll DescribeImages.
            ami.state = "pending"
            labels = _ami_labels(self, ami, source_instance=instance_id)

            def bake() -> None:
                started = time.monotonic()
                LOG.info("CreateImage %s -> %s: snapshotting (large instances take minutes)…",
                         instance_id, ami.id)
                try:
                    ref = VM.commit_instance(instance_id, ami.id, labels)  # type: ignore[union-attr]
                    ami.ec2sim_image = ref  # type: ignore[attr-defined]
                    ami.image_location = f"docker:{ref}"
                    ami.state = "available"
                    LOG.info("CreateImage %s -> %s available after %.0fs (%s)",
                             instance_id, ami.id, time.monotonic() - started, ref)
                except Exception:  # noqa: BLE001
                    LOG.exception("CreateImage %s -> %s failed", instance_id, ami.id)
                    ami.state = "failed"

            threading.Thread(target=bake, daemon=True, name=f"bake-{ami.id}").start()
        return ami

    @_wrap(AmiBackend, "copy_image")
    def copy_image(original, self: AmiBackend, source_image_id: str, source_region: str,
                   name: str | None = None, description: str | None = None):
        ami = original(self, source_image_id, source_region, name, description)
        ami.root_device_type = "ebs"
        from moto.ec2.models import ec2_backends

        src = ec2_backends[self.account_id][source_region].amis.get(source_image_id)  # type: ignore[attr-defined]
        src_ref = getattr(src, "ec2sim_image", None)
        if VM is not None and src_ref:
            ref = VM.register_docker_image(
                src_ref, ami.id, _ami_labels(self, ami, source_ami=source_image_id)
            )
            ami.ec2sim_image = ref  # type: ignore[attr-defined]
            ami.image_location = f"docker:{ref}"
        return ami

    @_wrap(AmiBackend, "deregister_image")
    def deregister_image(original, self: AmiBackend, ami_id: str):
        ami = self.amis.get(ami_id)
        original(self, ami_id)
        if VM is not None and ami is not None and ami_id not in BASE_BY_ID \
                and getattr(ami, "ec2sim_image", None):
            VM.remove_ami_image(ami_id)

    # RegisterImage: moto ignores ImageLocation; we accept docker://<image-ref>.
    @_wrap(AmisResponse, "register_image")
    def register_image(original, self: AmisResponse):
        location = self._get_param("ImageLocation") or ""
        arch = self._get_param("Architecture")
        result = original(self)
        image_id = result.result["ImageId"]  # type: ignore[attr-defined]
        backend = self.ec2_backend
        ami = backend.amis[image_id]
        ami.root_device_type = "ebs"
        ami.virtualization_type = "hvm"
        if arch:
            ami.architecture = arch
        if location:
            ami.image_location = location
        if VM is not None and location.startswith("docker://"):
            ref = VM.register_docker_image(
                location[len("docker://"):], image_id,
                _ami_labels(backend, ami, source_image=location),
            )
            ami.ec2sim_image = ref  # type: ignore[attr-defined]
        return result

    # ---------------------------------------------------------- custom IDs
    # Tag `_custom_id_` on CreateSecurityGroup picks the group ID, so scripts
    # that hard-code real IDs (e.g. sg-138d3374) find their group.
    import moto.ec2.models.security_groups as sg_module
    from moto.ec2.models.security_groups import SecurityGroupBackend

    sg_lock = threading.Lock()

    @_wrap(SecurityGroupBackend, "create_security_group")
    def create_security_group(original, self: Any, name: str, description: str,
                              vpc_id: str | None = None, tags: dict[str, str] | None = None,
                              *args: Any, **kwargs: Any):
        custom = (tags or {}).get("_custom_id_")
        if not custom:
            return original(self, name, description, vpc_id, tags, *args, **kwargs)
        tags = {k: v for k, v in tags.items() if k != "_custom_id_"}
        with sg_lock:
            saved = sg_module.random_security_group_id
            sg_module.random_security_group_id = lambda: custom
            try:
                return original(self, name, description, vpc_id, tags, *args, **kwargs)
            finally:
                sg_module.random_security_group_id = saved

    # ------------------------------------------------------------- instances
    @_wrap(InstanceBackend, "run_instances")
    def run_instances(original, self: Any, image_id: str, count: int, user_data: Any,
                      security_group_names: list[str], **kwargs: Any):
        reservation = original(self, image_id, count, user_data, security_group_names, **kwargs)
        if VM is None:
            return reservation
        ami = self.amis.get(image_id)
        image_ref = getattr(ami, "ec2sim_image", None)
        if image_ref is None:
            LOG.info("AMI %s has no Docker image; instances stay mock-only", image_id)
            return reservation
        if image_id in BASE_BY_ID:
            VM.ensure_base_image(BASE_BY_ID[image_id])
        for inst in reservation.instances:
            _boot(self, inst, image_ref)
        return reservation

    def _boot(backend: Any, inst: Instance, image_ref: str) -> None:
        pubkey = None
        if inst.key_name and inst.key_name in backend.key_pairs:
            pubkey = backend.key_pairs[inst.key_name].public_key
        cidr = None
        if inst.subnet_id:
            try:
                cidr = backend.get_subnet(inst.subnet_id).cidr_block
            except Exception:  # noqa: BLE001
                cidr = None
        try:
            ip = VM.run_instance(  # type: ignore[union-attr]
                instance_id=inst.id,
                account_id=backend.account_id,
                region=backend.region_name,
                image_ref=image_ref,
                subnet_id=inst.subnet_id,
                subnet_cidr=cidr,
                private_ip=inst.private_ip,
                ssh_pubkey=pubkey,
                user_data_b64=_user_data_b64(inst.user_data),
                instance_type=inst.instance_type,
            )
            if ip and cidr and ipaddress.ip_address(ip) in ipaddress.ip_network(cidr, strict=False):
                # Keep moto's view truthful: the private IP is the container's subnet IP.
                inst.nics[0].private_ip_address = ip
            _set_public_ip(inst, ip)
            LOG.info("RunInstances %s -> container at %s (%s)", inst.id, ip, image_ref)
        except Exception as e:  # noqa: BLE001
            LOG.exception("could not start container for %s", inst.id)
            Instance.terminate.__wrapped__(inst)  # type: ignore[attr-defined]
            inst._reason = f"Server.InternalError: {e}"

    @_wrap(Instance, "stop")
    def stop(original, self: Instance):
        prev = original(self)
        if VM is not None:
            VM.stop_instance(self.id)
        return prev

    @_wrap(Instance, "start")
    def start(original, self: Instance):
        prev = original(self)
        if VM is not None:
            _set_public_ip(self, VM.start_instance(self.id))
        return prev

    @_wrap(Instance, "reboot")
    def reboot(original, self: Instance):
        original(self)
        if VM is not None:
            VM.reboot_instance(self.id)

    @_wrap(Instance, "terminate")
    def terminate(original, self: Instance):
        prev = original(self)
        if VM is not None:
            VM.terminate_instance(self.id)
        return prev
