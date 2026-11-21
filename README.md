# ec2-sim

A local EC2 simulator where **instances are real Docker containers and AMIs
are real Docker images**. You can build AMIs with Packer, SSH into instances,
run your provisioning scripts, and launch what you baked, all on your machine
and without an AWS account.

Why use it, what it's for, and what it saves: [docs/why-ec2-sim.md](docs/why-ec2-sim.md).

Prefer clicking? The [desktop app](ui/README.md) (Tauri) starts the simulator,
launches and connects to instances, and bakes AMIs from a GUI.

| EC2 | ec2-sim |
|---|---|
| AMI | Docker image (`ec2sim/base-*` or `ec2sim/ami:<ami-id>`) |
| `RunInstances` | `docker run` on a per-subnet bridge network, at a subnet IP |
| key pair / user data | public key → `authorized_keys`; user data runs once per instance |
| `Stop`/`Start`/`Reboot`/`Terminate` | `docker stop`/`start`/`restart`/`rm` |
| **`CreateImage`** | **`docker commit`** → `ec2sim/ami:<ami-id>` |
| `CopyImage` | re-labelled image in the target region |
| `RegisterImage --image-location docker://<ref>` | wrap any Docker image as an AMI |
| `DeregisterImage` | `docker rmi` |
| Auto Scaling groups / classic ELB | ASG-launched instances are real containers; ELB health = container running |
| instance metadata (169.254.169.254) | IMDSv1/v2: meta-data, user-data, identity document, instance-profile credentials |
| real AWS hostnames (`ec2.<region>.amazonaws.com`, …) | HTTPS proxy, for tools you can't point at a custom endpoint |
| everything else (VPC, SG, IAM, tags, Describe*…) | [moto](https://github.com/getmoto/moto), in memory |

moto owns the API surface (request parsing, validation, responses, filters).
ec2-sim wraps moto's EC2 model methods (`ec2sim/patches.py`) so lifecycle calls
also drive Docker (`ec2sim/vm.py`).

## Requirements

- Docker: native Docker Engine on Linux or WSL2, OrbStack, or Docker Desktop on
  macOS/Windows (see [macOS and Docker Desktop](#macos-and-docker-desktop))
- [uv](https://docs.astral.sh/uv/) and Python 3.12+
- AWS CLI (optional, for the examples and tests)

## Quick start

```bash
uv sync
uv run ec2-sim build-images      # optional: pre-build the base AMIs (otherwise built at first start)
uv run ec2-sim                   # API on http://127.0.0.1:4566
```

In another terminal:

```bash
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-1
alias ec2='aws --endpoint-url http://localhost:4566 ec2'

ec2 describe-images --owners amazon 099720109477 --filters Name=name,Values='*ec2sim*'
ec2 create-key-pair --key-name me --query KeyMaterial --output text > me.pem && chmod 600 me.pem
ec2 run-instances --image-id ami-0ec2511a120230001 --instance-type t3.micro --key-name me \
    --user-data file://bootstrap.sh
ec2 describe-instances --query 'Reservations[].Instances[].[InstanceId,State.Name,PublicIpAddress]'
ssh -i me.pem ec2-user@<PublicIpAddress>

ec2 stop-instances --instance-ids i-...
ec2 create-image --instance-id i-... --name my-ami       # bake the instance into an AMI
ec2 run-instances --image-id ami-<new> --instance-type t3.micro --key-name me
ec2 terminate-instances --instance-ids i-...
```

Ctrl+C stops the server and removes all instance containers. AMIs you created are kept.

## macOS and Docker Desktop

ec2-sim needs to reach instance IPs (e.g. `172.31.0.4`) from the host for SSH.
That works with native Docker Engine (Linux, WSL2) and OrbStack. Docker
Desktop runs containers inside a hidden VM, so those IPs **aren't routable
from macOS or Windows**, and SSH (including Packer's) times out.

ec2-sim detects Docker Desktop and switches to **bastion mode** automatically.
It starts an SSH jump host on `127.0.0.1:2222` (user `ec2sim`, password
`ec2sim`) that is attached to every instance network. Connect to instances
through it; their IPs and everything else stay the same:

```bash
ssh -J ec2sim@127.0.0.1:2222 -i me.pem ec2-user@172.31.0.4
```

Packer: add the bastion to the `amazon-ebs` source:

```hcl
  ssh_bastion_host     = "127.0.0.1"
  ssh_bastion_port     = 2222
  ssh_bastion_username = "ec2sim"
  ssh_bastion_password = "ec2sim"
```

To use a key instead of the password, set `EC2SIM_BASTION_PUBKEY` to your
public key before starting ec2-sim, then use `ssh_bastion_private_key_file`
in Packer. The tests take the bastion via `EC2SIM_BASTION=127.0.0.1:2222`
(`tests/smoke.sh` also needs `sshpass`).

Notes:

- The bastion only listens on `127.0.0.1`. Override detection with
  `EC2SIM_NETWORK_MODE=routed|bastion`.
- In bastion mode the metadata service backend listens on `127.0.0.1` and
  instances reach it through `host.docker.internal`.
- Apple Silicon builds arm64 base images, and AMIs report `arm64`. Use
  `architecture = arm64` in AMI filters, or no architecture filter.
- Tools that run inside a container on the same Docker networks (or with
  `--network host` in the Docker Desktop VM) can reach instance IPs without the bastion.

## Base AMIs

| AMI | Image | SSH user |
|---|---|---|
| `ami-0ec2511a120230001` | Amazon Linux 2023 (owner `amazon`) | `ec2-user` |
| `ami-0ec2511b240400001` | Ubuntu 24.04 (owner `099720109477`) | `ubuntu` |
| `ami-0ec2511b180400001` | Ubuntu 18.04 **with systemd as PID 1** (owner `099720109477`) | `ubuntu` |
| `ami-0ec2511b260400001` | Ubuntu 26.04 **with systemd as PID 1** (owner `099720109477`) | `ubuntu` |

All are EBS-backed, HVM, use the Docker engine's architecture (`x86_64`, or
`arm64` on Apple Silicon/ARM hosts), and match the usual name filters
(`al2023-ami-*`, `ubuntu/images/*`).

**systemd AMIs.** Images labelled `ec2sim.systemd=true`, and every AMI baked
from one, run privileged with their own cgroup namespace, so `systemctl
enable/start` and services behave as on a VM. User data runs from a systemd
unit. Other AMIs use a small init script and run without systemd (start
services from user data).

**Aliases for hard-coded AMI IDs.** If a script references a specific AMI ID,
expose a base image under that ID:

```bash
EC2SIM_AMI_ALIASES=ami-0123456789abcdef0=ubuntu-18.04,ami-0fedcba9876543210=amazonlinux-2023 uv run ec2-sim
```

## Building AMIs with Packer

Point the `amazon-ebs` builder at ec2-sim:

```hcl
source "amazon-ebs" "example" {
  region                     = "us-east-1"
  access_key                 = "test"
  secret_key                 = "test"
  custom_endpoint_ec2        = "http://localhost:4566"
  skip_credential_validation = true
  skip_region_validation     = true
  skip_metadata_api_check    = true
  ssh_interface              = "public_ip"
  # ...source_ami / source_ami_filter, instance_type, ssh_username, ami_name
}
```

Working example: `tests/e2e/al2023-web.pkr.hcl` (`./tests/e2e/run-packer.sh`).
On Docker Desktop, also add the bastion settings from
[macOS and Docker Desktop](#macos-and-docker-desktop).

## Running tools that only talk to real AWS

Some scripts and templates have no endpoint setting. ec2-sim also runs an HTTPS
proxy (port 4567) that answers `*.amazonaws.com` from the same simulated state:

```bash
export HTTPS_PROXY=http://127.0.0.1:4567
export AWS_CA_BUNDLE=$(uv run ec2-sim ca-path)   # AWS CLI / boto3
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test
aws ec2 describe-instances                      # no --endpoint-url
```

Go tools (Packer, Terraform) read the system trust store: add the CA from
`ec2-sim ca-path` there (e.g. `update-ca-certificates`).

**Use only fake credentials** in that environment, so anything that bypasses
the proxy fails instead of reaching AWS. Running the tool in a container that
has no real credentials is the safest setup.

## Running an existing pipeline unmodified

To rehearse an existing AMI build or rollout script locally:

1. **Base AMI:** alias the script's source AMI ID to a base image (`EC2SIM_AMI_ALIASES`).
   Use the systemd image if provisioning enables services.
2. **Seed** the resources the script expects to already exist (IAM instance
   profiles, key pairs, security groups, load balancers, ASGs) with the AWS CLI
   against `http://localhost:4566`. Use `_custom_id_` (below) when the script
   hard-codes a resource ID.
3. **Run** the script with `HTTPS_PROXY`, the ec2-sim CA, and fake credentials,
   ideally in a container on the host network (so it can SSH to instance IPs)
   that contains no real credentials.

Provisioning runs for real inside the instances, so packages are downloaded
from wherever your scripts point.

## Running your own scripts: `ec2-sim run`

Run a project's own build/deploy command, **unmodified**, against ec2-sim:

```bash
export VAULT_TOKEN=...            # any variables your script needs
uv run ec2-sim run -C /path/to/repo --region us-west-2 -e VAULT_TOKEN -- './build-ami.sh --env staging'
```

The command runs in a disposable runner container (`images/runner`: Packer +
Amazon plugin, AWS CLI v2, jq, sudo, `tempfile`) with:

- the folder mounted **at the same path** (relative paths and log files behave as on a build host)
- AWS calls routed to ec2-sim through its HTTPS proxy, with the CA trusted: no endpoint changes needed
- **only fake AWS credentials** (your real ones are never passed in), on the host network for SSH to instances
- variables passed by name (`-e NAME` takes the value from your environment), so secrets stay off command lines

`ec2-sim stop-runs` stops running commands. The desktop app's **Run script** page does the same from the GUI.
Scripts must have LF line endings: on Windows, run them from a checkout inside WSL.

## Networking: instance ↔ instance and host → instance

- **Inside a VPC** all instances share one Docker network (the VPC's CIDR), so
  they reach each other across subnets/AZs, as in AWS. IPs are still allocated
  in each subnet's range; `--private-ip-address` is honoured. Instances resolve
  each other by **instance ID**, **Name tag**, and **private DNS name**
  (`ip-10-0-1-4.<region>.compute.internal`, `…ec2.internal` in us-east-1).
  Different VPCs stay isolated.
- **From your computer** (any OS) use a port forward:

  ```bash
  uv run ec2-sim forward i-0abc… 5432 --local 15432   # localhost:15432 -> instance:5432
  uv run ec2-sim forwards                              # list
  uv run ec2-sim unforward ec2sim-fwd-…                # remove (or --all)
  ```

  Docker publishes it on the host's localhost: on WSL it's also reachable from
  Windows, on Docker Desktop from macOS/Windows. Forwards are removed when the
  instance is terminated or the server stops. In the desktop app:
  **Instances → ⋯ → Port forward…**.
- **Web apps by name** through the HTTP gateway (on by default, port 80):
  `http://<instance-name>.localhost/` → that instance's port 80, and
  `http://<name>.localhost:9404/` → port 9404 once that port is added:

  ```bash
  uv run ec2-sim gateway add-port 9404   # saved in ~/.ec2-sim/gateway-ports
  uv run ec2-sim gateway                 # list ports
  ```

  The name is the instance's **Name tag** (lowercased) or its instance ID.
  `*.localhost` resolves to your own machine in browsers and curl on Windows,
  macOS and Linux, so no DNS or hosts-file setup is needed. Apps must listen on
  `0.0.0.0`. For bare names (`http://app2:9404`) add hosts-file entries pointing
  to 127.0.0.1 (needs admin). In the app: **Connect → Browser**.
- On Linux/WSL hosts instance IPs are also directly routable from the host itself.

## Custom resource IDs

Tag `_custom_id_` on `CreateSecurityGroup` to choose the group ID:

```bash
ec2 create-security-group --group-name app --description app \
  --tag-specifications 'ResourceType=security-group,Tags=[{Key=_custom_id_,Value=sg-0123abcd}]'
```

## Instance metadata

Inside every instance, `http://169.254.169.254/` serves IMDSv1 and IMDSv2
(`PUT /latest/api/token`), under `/latest/` and dated versions:
`meta-data/*` (instance-id, ami-id, local-ipv4, placement, …), `user-data`,
`dynamic/instance-identity/document`, and
`meta-data/iam/security-credentials/<role>` when the instance has an instance
profile (directly or through an ASG launch configuration). Tools such as
`ec2metadata` and the AWS SDKs work unchanged.

## Looking inside

```bash
docker ps --filter label=ec2sim.instance_id         # instances (container name: ec2sim-<instance-id>)
docker exec -it ec2sim-i-xxxx bash                  # shell without SSH
docker exec ec2sim-i-xxxx systemctl --failed        # systemd AMIs
docker exec ec2sim-i-xxxx cat /var/log/cloud-init-output.log   # user-data output
docker images ec2sim/ami                            # AMIs you've baked
```

## Tests

```bash
./tests/smoke.sh            # AWS CLI: run → user data → SSH → bake → CreateImage → relaunch → deregister
./tests/e2e/run-packer.sh   # Packer amazon-ebs build of an nginx AMI
```

Both expect a running server on `localhost:4566`. In bastion mode, run them with
`EC2SIM_BASTION=127.0.0.1:2222`.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `EC2SIM_HOST` / `EC2SIM_PORT` | `127.0.0.1` / `4566` | API listen address |
| `EC2SIM_VM_MANAGER` | `docker` | `mock` = pure moto, no containers |
| `EC2SIM_REMOVE_CONTAINERS` | `true` | remove containers on terminate/shutdown |
| `EC2SIM_BUILD_BASE_IMAGES` | `true` | build base images in the background at start |
| `EC2SIM_DOCKER_RUN_EXTRA` | – | JSON kwargs for `docker run`, e.g. `{"privileged": true}` |
| `EC2SIM_STOP_TIMEOUT` | `5` | seconds before SIGKILL on stop |
| `EC2SIM_PROXY_PORT` | `4567` | HTTPS proxy for real AWS hostnames (0 = off) |
| `EC2SIM_IMDS_PORT` | `4569` | IMDS backend port on the IMDS network gateway (0 = off) |
| `EC2SIM_AMI_ALIASES` | – | `ami-id=base-image,...` extra IDs for base AMIs |
| `EC2SIM_NETWORK_MODE` | `auto` | `routed` (host reaches instance IPs) or `bastion` (Docker Desktop) |
| `EC2SIM_BASTION_PORT` | `2222` | bastion SSH port on `127.0.0.1` (bastion mode) |
| `EC2SIM_BASTION_PUBKEY` | – | public key(s) allowed to log in to the bastion as `ec2sim` |
| `EC2SIM_IMDS_BIND` | `127.0.0.1` | IMDS backend bind address in bastion mode |
| `EC2SIM_GATEWAY_PORTS` | `80` | HTTP gateway ports (`""` = off); more via `ec2-sim gateway add-port` |
| `EC2SIM_GATEWAY_BIND` | `127.0.0.1` | address the gateway publishes on |

Commands: `ec2-sim` (serve), `ec2-sim build-images`, `ec2-sim cleanup`
(remove stray instance containers and networks, keep AMIs), `ec2-sim ca-path`.

## State and persistence

- Instances, VPCs, key pairs and the rest of the moto state live in memory;
  instance containers are removed when the server stops.
- AMIs you create persist as labelled Docker images (`ec2sim.ami_id`, account,
  region, name) and are restored on the next start, scoped to account and region.
  `DeregisterImage` deletes the image.

## Known limits

- Containers, not VMs: shared host kernel, no custom kernels or kernel modules,
  no Windows AMIs.
- The public IP is the container's subnet IP: reachable from the host with
  native Docker/OrbStack, and through the bastion on Docker Desktop.
- Security groups are recorded but not enforced.
- AMI tags and EBS volumes/snapshots are metadata only: not restored after a
  restart and not captured by `CreateImage`.
- Instance-profile credentials are random strings: accepted by ec2-sim, not by
  real AWS STS or services that verify them there.
- Not implemented: Launch Template-backed ASG details, EC2 Image Builder.

## Adding a base AMI

1. Add `images/<name>/Dockerfile`: install sshd and sudo, create the default user,
   copy `common/ec2sim-init` as the entrypoint and set `EC2SIM_DEFAULT_USER`. For a
   systemd image, follow `images/ubuntu-18.04` (`ec2sim-boot` entrypoint,
   `ec2sim-userdata.service`, `LABEL ec2sim.systemd="true"`).
2. Add a `BaseAmi` entry in `ec2sim/catalog.py`.

## License

Apache License 2.0, see [LICENSE](LICENSE). Third-party components (moto,
Docker SDK, upstream base images, optional test tools) keep their own licenses;
see [NOTICE](NOTICE). ec2-sim is not affiliated with or endorsed by Amazon Web Services.
