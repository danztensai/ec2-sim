"""`ec2-sim run`: run a project's own scripts, unmodified, against ec2-sim.

The command runs in a disposable runner container (images/runner: Packer +
Amazon plugin, AWS CLI v2, jq, sudo, ...) with:

- the working directory mounted at the same path, so relative paths and log
  files behave exactly as on a build host;
- AWS traffic sent through ec2-sim's HTTPS proxy (real AWS hostnames work, no
  endpoint flags needed) and the ec2-sim CA trusted;
- only fake AWS credentials (host credentials are never passed in);
- the host network, so tools can SSH to instance IPs.

Extra variables are passed by name (`-e NAME`, value taken from the
environment) so secrets never appear on a command line.
"""

from __future__ import annotations

import os
import secrets
import signal
import sys
import urllib.request

from . import config

RUNNER_IMAGE = "ec2sim/runner:latest"
LABEL = f"{config.LABEL}.runner"


def _server_up() -> bool:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{config.PORT}/", timeout=2)
        return True
    except Exception as e:  # noqa: BLE001
        return getattr(e, "code", None) is not None  # any HTTP answer means it's up


def _ensure_image(client) -> None:
    from docker.errors import ImageNotFound

    try:
        client.images.get(RUNNER_IMAGE)
    except ImageNotFound:
        print(f"[ec2-sim run] building {RUNNER_IMAGE} (first use, a few minutes)…", flush=True)
        client.images.build(path=str(config.IMAGES_DIR), dockerfile="runner/Dockerfile", tag=RUNNER_IMAGE, rm=True)


def _parse_env(items: list[str]) -> dict[str, str]:
    env: dict[str, str] = {}
    for item in items:
        name, eq, value = item.partition("=")
        if not name.replace("_", "").isalnum():
            sys.exit(f"invalid variable name: {name!r}")
        if eq:
            env[name] = value
        elif name in os.environ:
            env[name] = os.environ[name]
        else:
            sys.exit(f"-e {name}: not set in the environment")
    return env


def run(workdir: str, command: list[str], env_items: list[str], region: str) -> int:
    import docker
    from moto.moto_proxy.proxy3 import CertificateCreator

    workdir = os.path.realpath(workdir)
    if not os.path.isdir(workdir):
        sys.exit(f"not a directory: {workdir}")
    if not command:
        sys.exit("nothing to run: ec2-sim run [options] -- COMMAND …")
    if not _server_up():
        sys.exit(f"ec2-sim is not running on port {config.PORT}; start it first")

    client = docker.from_env()
    _ensure_image(client)

    # A single argument is a shell command line ("./build.sh -e staging -a").
    argv = ["bash", "-c", command[0]] if len(command) == 1 else command
    environment = {
        # Fake credentials only: anything that bypasses the proxy fails instead of reaching AWS.
        "AWS_ACCESS_KEY_ID": "test",
        "AWS_SECRET_ACCESS_KEY": "test",
        "AWS_SESSION_TOKEN": "",
        "AWS_DEFAULT_REGION": region,
        "AWS_REGION": region,
        "HTTPS_PROXY": f"http://127.0.0.1:{config.PROXY_PORT}",
        "https_proxy": f"http://127.0.0.1:{config.PROXY_PORT}",
        "NO_PROXY": "localhost,127.0.0.1,169.254.169.254",
        "no_proxy": "localhost,127.0.0.1,169.254.169.254",
        "AWS_CA_BUNDLE": "/etc/ssl/certs/ca-certificates.crt",
        "CHECKPOINT_DISABLE": "1",
        "PACKER_NO_COLOR": "1",
    }
    environment.update(_parse_env(env_items))

    name = f"{config.PREFIX}-run-{secrets.token_hex(3)}"
    print(f"[ec2-sim run] {workdir}$ {' '.join(command)}  (region {region}, container {name})", flush=True)
    container = client.containers.run(
        RUNNER_IMAGE,
        argv,
        name=name,
        detach=True,
        network_mode="host",
        working_dir=workdir,
        volumes={
            workdir: {"bind": workdir, "mode": "rw"},
            CertificateCreator.cacert: {"bind": "/ec2sim-ca.crt", "mode": "ro"},
        },
        environment=environment,
        labels={LABEL: "true"},
    )

    def stop(*_):
        print("\n[ec2-sim run] stopping…", flush=True)
        try:
            container.kill()
        except Exception:  # noqa: BLE001
            pass

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        for chunk in container.logs(stream=True, follow=True):
            sys.stdout.write(chunk.decode(errors="replace"))
            sys.stdout.flush()
        code = container.wait().get("StatusCode", 1)
    finally:
        try:
            container.remove(force=True)
        except Exception:  # noqa: BLE001
            pass
    print(f"[ec2-sim run] exit code {code}", flush=True)
    return int(code)


def stop_all() -> int:
    """Kill every running `ec2-sim run` container."""
    import docker

    client = docker.from_env()
    n = 0
    for c in client.containers.list(filters={"label": LABEL}):
        c.kill()
        n += 1
    print(f"[ec2-sim run] stopped {n} run(s)")
    return 0
