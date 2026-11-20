"""Runtime settings, all overridable through environment variables."""

import os
from pathlib import Path


def _flag(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() not in ("0", "false", "no", "off", "")


HOST = os.environ.get("EC2SIM_HOST", "127.0.0.1")
PORT = int(os.environ.get("EC2SIM_PORT", "4566"))

# "docker" runs real containers; "mock" keeps moto's pure in-memory behaviour.
VM_MANAGER = os.environ.get("EC2SIM_VM_MANAGER", "docker").strip().lower()

# Remove instance containers on terminate / shutdown (False: only stop them).
REMOVE_CONTAINERS = _flag("EC2SIM_REMOVE_CONTAINERS", True)

# Build the base AMI images in the background when the server starts.
BUILD_BASE_IMAGES = _flag("EC2SIM_BUILD_BASE_IMAGES", True)

# Extra `docker run` options as JSON, e.g. '{"privileged": true}'.
DOCKER_RUN_EXTRA = os.environ.get("EC2SIM_DOCKER_RUN_EXTRA", "")

# Seconds `docker stop` waits before SIGKILL.
STOP_TIMEOUT = int(os.environ.get("EC2SIM_STOP_TIMEOUT", "5"))

# HTTPS proxy that serves real AWS hostnames (ec2.us-west-2.amazonaws.com, ...)
# from ec2-sim, so unmodified tools work with HTTPS_PROXY + the ec2-sim CA. 0 = off.
PROXY_PORT = int(os.environ.get("EC2SIM_PROXY_PORT", "4567"))

# Instance metadata service at 169.254.169.254 inside instances. 0 = off.
IMDS_PORT = int(os.environ.get("EC2SIM_IMDS_PORT", "4569"))
IMDS_SUBNET = os.environ.get("EC2SIM_IMDS_SUBNET", "169.254.169.0/24")
IMDS_GATEWAY = os.environ.get("EC2SIM_IMDS_GATEWAY", "169.254.169.1")
IMDS_ADDRESS = "169.254.169.254"
IMDS_PROXY_IMAGE = os.environ.get("EC2SIM_IMDS_PROXY_IMAGE", "nginx:1.27-alpine")

IMAGES_DIR = Path(__file__).resolve().parent.parent / "images"

# Prefix for every Docker object ec2-sim creates.
PREFIX = "ec2sim"
LABEL = "ec2sim"  # label namespace: ec2sim.<key>
AMI_REPO = f"{PREFIX}/ami"
