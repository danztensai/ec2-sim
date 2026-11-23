"""Base AMIs that ec2-sim ships, each backed by a Docker image built from ec2sim/images/<dir>."""

import os
from dataclasses import dataclass, replace
from datetime import UTC, datetime


@dataclass(frozen=True)
class BaseAmi:
    ami_id: str
    name: str
    description: str
    owner_id: str
    owner_alias: str
    image_dir: str  # directory under ec2sim/images/
    default_user: str

    @property
    def docker_image(self) -> str:
        return f"ec2sim/base-{self.image_dir}:latest"


BASE_AMIS: list[BaseAmi] = [
    BaseAmi(
        ami_id="ami-0ec2511a120230001",
        name="al2023-ami-2023.ec2sim-kernel-6.1-x86_64",
        description="Amazon Linux 2023 (ec2-sim container AMI)",
        owner_id="137112412989",
        owner_alias="amazon",
        image_dir="amazonlinux-2023",
        default_user="ec2-user",
    ),
    BaseAmi(
        ami_id="ami-0ec2511b240400001",
        name="ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-ec2sim",
        description="Canonical, Ubuntu, 24.04 LTS (ec2-sim container AMI)",
        owner_id="099720109477",
        owner_alias="",
        image_dir="ubuntu-24.04",
        default_user="ubuntu",
    ),
    BaseAmi(
        ami_id="ami-0ec2511b180400001",
        name="ubuntu/images/hvm-ssd/ubuntu-bionic-18.04-amd64-server-ec2sim",
        description="Canonical, Ubuntu, 18.04 LTS with systemd (ec2-sim container AMI)",
        owner_id="099720109477",
        owner_alias="",
        image_dir="ubuntu-18.04",
        default_user="ubuntu",
    ),
    BaseAmi(
        ami_id="ami-0ec2511b260400001",
        name="ubuntu/images/hvm-ssd-gp3/ubuntu-resolute-26.04-amd64-server-ec2sim",
        description="Canonical, Ubuntu, 26.04 LTS with systemd (ec2-sim container AMI)",
        owner_id="099720109477",
        owner_alias="",
        image_dir="ubuntu-26.04",
        default_user="ubuntu",
    ),
]


def _aliases() -> list[BaseAmi]:
    """EC2SIM_AMI_ALIASES="ami-0a588942e90cfecc9=ubuntu-18.04,ami-...=amazonlinux-2023"

    Exposes a base image under a real-world AMI ID so scripts that hard-code
    one (e.g. a BASEAMI in a build config) work unmodified."""
    by_dir = {b.image_dir: b for b in BASE_AMIS}
    out = []
    for item in filter(None, (x.strip() for x in os.environ.get("EC2SIM_AMI_ALIASES", "").split(","))):
        ami_id, _, image_dir = item.partition("=")
        if image_dir not in by_dir:
            raise SystemExit(f"EC2SIM_AMI_ALIASES: unknown base image '{image_dir}' "
                             f"(known: {', '.join(by_dir)})")
        out.append(replace(by_dir[image_dir], ami_id=ami_id.strip()))
    return out


BASE_AMIS += _aliases()
BASE_BY_ID = {b.ami_id: b for b in BASE_AMIS}
BASE_CREATION_DATE = datetime(2026, 1, 1, tzinfo=UTC)
