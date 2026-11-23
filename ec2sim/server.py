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


def gateway_ports_wanted() -> list[int]:
    """EC2SIM_GATEWAY_PORTS plus ports added with `ec2-sim gateway add-port`."""
    ports = {int(p) for p in config.GATEWAY_PORTS.replace(" ", "").split(",") if p.isdigit()}
    try:
        ports |= {int(p) for p in config.GATEWAY_PORTS_FILE.read_text().split() if p.isdigit()}
    except FileNotFoundError:
        pass
    return sorted(ports)


def _gateway_cmd(args) -> int:
    from .vm import DockerVMManager

    logging.getLogger("ec2sim").setLevel(logging.WARNING)
    vm = DockerVMManager()
    saved = set()
    try:
        saved = {int(p) for p in config.GATEWAY_PORTS_FILE.read_text().split() if p.isdigit()}
    except FileNotFoundError:
        pass
    if args.action in ("add-port", "remove-port"):
        if not args.port or not 0 < args.port < 65536:
            print("give a port number", file=sys.stderr)
            return 2
        (saved.add if args.action == "add-port" else saved.discard)(args.port)
        config.GATEWAY_PORTS_FILE.parent.mkdir(parents=True, exist_ok=True)
        config.GATEWAY_PORTS_FILE.write_text("\n".join(map(str, sorted(saved))) + "\n")
        wanted = gateway_ports_wanted()
        if args.action == "remove-port" and args.port in wanted:
            wanted.remove(args.port)  # also drop it if it only came from the env default
        published = vm.ensure_gateway(wanted)
        if args.action == "add-port" and args.port not in published:
            print(f"error: host port {args.port} is in use", file=sys.stderr)
            return 1
    ports = vm.gateway_ports()
    if args.json:
        import json as _json

        g = vm._gateway()
        mode = (g.labels.get(f"{config.LABEL}.gateway_access") if g else None) or config.GATEWAY_ACCESS
        print(_json.dumps({"ports": ports, "bind": config.GATEWAY_BIND, "access": mode}))
    elif ports:
        for p in ports:
            print(f"http://<instance-name>.localhost{'' if p == 80 else f':{p}'}/  ->  instance port {p}")
    else:
        print("HTTP gateway is not running")
    return 0


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
    gw = sub.add_parser("gateway", help="HTTP gateway: http://<instance-name>.localhost[:PORT]")
    gw.add_argument("action", nargs="?", default="list", choices=["list", "add-port", "remove-port"])
    gw.add_argument("port", nargs="?", type=int)
    gw.add_argument("--json", action="store_true")
    uf = sub.add_parser("unforward", help="remove a port forward (NAME from `forwards`) or --all")
    uf.add_argument("name", nargs="?")
    uf.add_argument("--all", action="store_true")
    for name, text in (("up", "create a stack's missing resources and wait until it's ready"),
                       ("down", "remove everything a stack created"),
                       ("reset", "down, then up"),
                       ("status", "show a stack's instances, IPs and readiness"),
                       ("validate", "check a stack file without touching anything")):
        sp = sub.add_parser(name, help=text, description=f"{text}. See docs/stacks.md.")
        sp.add_argument("file", nargs="?", default="stack.yaml", help="stack file (default: stack.yaml)")
        sp.add_argument("--endpoint", help="ec2-sim API (default: http://127.0.0.1:EC2SIM_PORT)")
        if name in ("up", "reset"):
            sp.add_argument("--no-wait", action="store_true", help="don't wait for readiness checks")
        if name == "status":
            sp.add_argument("--json", action="store_true")
    parser.add_argument("--host", default=config.HOST)
    parser.add_argument("--port", type=int, default=config.PORT)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-5s %(name)s: %(message)s"
    )
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    if args.cmd in ("up", "down", "reset", "status", "validate"):
        from . import stack

        logging.getLogger("ec2sim").setLevel(logging.WARNING)
        sys.exit(stack.main(args.cmd, args.file, args.endpoint, wait=not getattr(args, "no_wait", False),
                            as_json=getattr(args, "json", False)))
    if args.cmd == "run":
        from . import run as run_mod

        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        sys.exit(run_mod.run(args.workdir, command, args.env, args.region))
    if args.cmd == "stop-runs":
        from . import run as run_mod

        sys.exit(run_mod.stop_all())
    if args.cmd == "gateway":
        sys.exit(_gateway_cmd(args))
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
        try:
            wanted = gateway_ports_wanted()
            if wanted:  # an empty list would remove a gateway another server runs
                vm.ensure_gateway(wanted)
                from . import access

                access.start_sync(vm)
        except Exception as e:  # noqa: BLE001 - the gateway is optional
            LOG.warning("HTTP gateway not started: %s", e)
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
