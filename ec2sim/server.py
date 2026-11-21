"""ec2-sim entry point: a moto server whose EC2 instances and AMIs are real Docker objects."""

from __future__ import annotations

import argparse
import atexit
import os
import logging
import signal
import sys
import threading

from . import config, patches

LOG = logging.getLogger("ec2sim")


def _make_vm():
    if config.VM_MANAGER == "mock":
        LOG.info("EC2SIM_VM_MANAGER=mock: pure in-memory EC2 (no containers)")
        return None
    try:
        from .vm import DockerVMManager

        return DockerVMManager()
    except Exception as e:  # noqa: BLE001
        LOG.warning("Docker unavailable (%s); falling back to mock EC2", e)
        return None


def _start_aws_proxy(host: str, port: int) -> None:
    """HTTPS proxy that terminates TLS for *.amazonaws.com with the ec2-sim CA and
    answers from the same in-process (patched) backends as the main endpoint."""
    from http.server import ThreadingHTTPServer

    from moto.moto_proxy.proxy3 import CertificateCreator, ProxyRequestHandler

    try:
        ProxyRequestHandler.validate()
        server = ThreadingHTTPServer((host, port), ProxyRequestHandler)
    except Exception as e:  # noqa: BLE001
        LOG.warning("AWS HTTPS proxy disabled: %s", e)
        return
    threading.Thread(target=server.serve_forever, daemon=True, name="aws-proxy").start()
    LOG.info("AWS HTTPS proxy on http://%s:%s (HTTPS_PROXY); CA: %s", host, port,
             CertificateCreator.cacert)


def _forward_cmd(args) -> int:
    import json as _json

    from .vm import DockerVMManager

    logging.getLogger("ec2sim").setLevel(logging.WARNING)  # keep CLI output clean
    vm = DockerVMManager()
    if args.cmd == "forward":
        local = args.local or (args.remote_port + 10000 if args.remote_port < 1024 else args.remote_port)
        try:
            f = vm.add_forward(args.instance_id, args.remote_port, local, args.bind)
        except RuntimeError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        print(f"{f['bind']}:{f['local_port']} -> {args.instance_id} ({f['target']})  [{f['name']}]")
        return 0
    if args.cmd == "forwards":
        fs = vm.list_forwards()
        if args.json:
            print(_json.dumps(fs))
        else:
            for f in fs:
                print(f"{f['bind']}:{f['local_port']:<6} -> {f['instance_id']}:{f['remote_port']:<6} {f['status']:<8} {f['name']}")
            if not fs:
                print("no port forwards")
        return 0
    if not args.name and not args.all:
        print("give a NAME (see `ec2-sim forwards`) or --all", file=sys.stderr)
        return 2
    n = vm.remove_forwards(name=None if args.all else args.name)
    print(f"removed {n} forward(s)")
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="ec2-sim", description=__doc__)
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("serve", help="run the API server (default)")
    sub.add_parser("build-images", help="build the base AMI Docker images and exit")
    sub.add_parser("cleanup", help="remove instance containers and networks, keep AMIs")
    sub.add_parser("ca-path", help="print the CA certificate path for the HTTPS proxy")
    run_p = sub.add_parser(
        "run",
        help="run a project's own command (e.g. a Packer build script) against ec2-sim",
        description="Run COMMAND unmodified in WORKDIR inside a runner container: AWS calls go to "
                    "ec2-sim via its HTTPS proxy, only fake AWS credentials exist.",
    )
    run_p.add_argument("--workdir", "-C", default=".", help="folder to run in (mounted at the same path)")
    run_p.add_argument("--region", default=os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
    run_p.add_argument("-e", "--env", action="append", default=[], metavar="NAME[=VALUE]",
                       help="pass a variable; NAME alone takes the value from your environment")
    run_p.add_argument("command", nargs=argparse.REMAINDER, help="-- COMMAND [ARGS…]")
    sub.add_parser("stop-runs", help="stop all running `ec2-sim run` commands")
    fwd = sub.add_parser("forward", help="publish localhost:PORT -> an instance port (any host OS)")
    fwd.add_argument("instance_id")
    fwd.add_argument("remote_port", type=int)
    fwd.add_argument("--local", type=int, help="host port (default: same, or +10000 below 1024)")
    fwd.add_argument("--bind", default="127.0.0.1", help="host address to listen on (default 127.0.0.1)")
    lf = sub.add_parser("forwards", help="list port forwards")
    lf.add_argument("--json", action="store_true")
    uf = sub.add_parser("unforward", help="remove a port forward (NAME from `forwards`) or --all")
    uf.add_argument("name", nargs="?")
    uf.add_argument("--all", action="store_true")
    parser.add_argument("--host", default=config.HOST)
    parser.add_argument("--port", type=int, default=config.PORT)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-5s %(name)s: %(message)s"
    )
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    if args.cmd == "run":
        from . import run as run_mod

        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        sys.exit(run_mod.run(args.workdir, command, args.env, args.region))
    if args.cmd == "stop-runs":
        from . import run as run_mod

        sys.exit(run_mod.stop_all())
    if args.cmd in ("forward", "forwards", "unforward"):
        sys.exit(_forward_cmd(args))
    if args.cmd == "ca-path":
        from moto.moto_proxy.proxy3 import CertificateCreator

        print(CertificateCreator.cacert)
        return

    vm = _make_vm()
    if args.cmd == "build-images":
        if vm is None:
            sys.exit("Docker is required to build images")
        vm.build_all_base_images()
        return
    if args.cmd == "cleanup":
        if vm is not None:
            vm.cleanup()
        return

    vm and vm.cleanup()  # leftovers from a crashed run; instances don't survive restarts
    patches.apply(vm)

    if vm is not None:
        atexit.register(vm.cleanup)
        if config.BUILD_BASE_IMAGES:
            threading.Thread(target=vm.build_all_base_images, daemon=True).start()
        if vm.network_mode == "bastion":
            vm.ensure_bastion()
        from . import imds

        imds.start(vm)
    if config.PROXY_PORT:
        _start_aws_proxy(args.host, config.PROXY_PORT)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))

    from moto.moto_server.werkzeug_app import DomainDispatcherApplication, create_backend_app
    from werkzeug.serving import run_simple

    LOG.info("ec2-sim listening on http://%s:%s (vm manager: %s)", args.host, args.port,
             "docker" if vm else "mock")
    run_simple(args.host, args.port, DomainDispatcherApplication(create_backend_app),
               threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()
