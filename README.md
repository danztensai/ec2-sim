# ec2-sim

A local EC2 simulator where **instances are real Docker containers and AMIs
are real Docker images**. You can build AMIs with Packer, SSH into instances,
run your provisioning scripts, and launch what you baked, all on your machine
and without an AWS account.

Why use it, what it's for, and what it saves: [docs/why-ec2-sim.md](docs/why-ec2-sim.md).

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

- Linux or WSL2 with a local Docker Engine (instance IPs must be reachable from the host)
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

## Base AMIs

| AMI | Image | SSH user |
|---|---|---|
| `ami-0ec2511a120230001` | Amazon Linux 2023 (owner `amazon`) | `ec2-user` |
| `ami-0ec2511b240400001` | Ubuntu 24.04 (owner `099720109477`) | `ubuntu` |
| `ami-0ec2511b180400001` | Ubuntu 18.04 **with systemd as PID 1** (owner `099720109477`) | `ubuntu` |

All are EBS-backed, HVM, x86_64, and match the usual name filters
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

Both expect a running server on `localhost:4566`.

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
- The public IP is the container's subnet IP (reachable from the Docker host).
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
