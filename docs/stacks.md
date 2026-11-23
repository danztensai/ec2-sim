# Stacks: a whole environment in one file

Building a test environment by hand takes a dozen AWS CLI calls: a VPC, a
subnet, a security group and its rules, key pairs, then each instance at the
right IP with its user data, in the right order. A **stack file** describes all
of it once:

```bash
uv run ec2-sim up stack.yaml        # create what's missing, wait until it's ready
uv run ec2-sim status stack.yaml    # instances, IPs, readiness
uv run ec2-sim reset stack.yaml     # tear down and recreate (a clean slate)
uv run ec2-sim down stack.yaml      # remove everything the stack created
uv run ec2-sim validate stack.yaml  # check the file, touch nothing
```

The file name defaults to `stack.yaml` in the current folder. The server must be
running (`uv run ec2-sim`). `validate` doesn't need it.

## A first stack

[`examples/stacks/hello/stack.yaml`](../examples/stacks/hello/stack.yaml) starts
a "db" instance, waits until it's ready, then starts "app", which reaches db by name:

```yaml
name: hello
region: us-west-2
key_pairs: [hello]

vpcs:
  - name: main
    cidr: 10.9.0.0/16
    subnets:
      - {name: app, cidr: 10.9.1.0/24}

security_groups:
  - name: stack
    vpc: main
    ingress:
      - {protocol: all, cidr: 10.9.0.0/16}
      - {port: 80, cidr: 0.0.0.0/0}

instances:
  - name: db
    ami: ubuntu-24.04
    subnet: app
    ip: 10.9.1.128
    security_groups: [stack]
    key: hello
    user_data: |
      #!/bin/bash
      sleep 3
      touch /tmp/db-ready
    ready: {command: "test -f /tmp/db-ready", timeout: 120}
    forwards: [{port: 22, local: 12222}]

  - name: app
    ami: ubuntu-24.04
    subnet: app
    security_groups: [stack]
    key: hello
    depends_on: [db]
    user_data: |
      #!/bin/bash
      getent hosts db > /tmp/db-address
    ready: {command: "grep -q 10.9.1.128 /tmp/db-address", timeout: 120}
```

```text
$ uv run ec2-sim up examples/stacks/hello/stack.yaml
# stack hello (us-west-2)
  key pair hello: created, private key ~/.ec2-sim/stacks/hello/hello.pem
  vpc main: created vpc-77eaeda1d91a57f3d 10.9.0.0/16
  subnet app: created subnet-e95ffe1777e51391f 10.9.1.0/24 (us-west-2a)
  security group stack: created sg-c767738b2d69d05b7
  instance db: launched i-4e6f37a7293485e9b at 10.9.1.128 (ami-0ec2511b240400001, t3.micro)
  waiting for db (command, up to 120s)…
  db: ready after 4s
  instance app: launched i-7afb7ba745a956fcb at 10.9.1.4 (ami-0ec2511b240400001, t3.micro)
  forward 127.0.0.1:12222 -> db:22
  waiting for app (command, up to 120s)…
  app: ready after 0s
# stack hello is up
NAME                 INSTANCE             STATE      IP               READY
db                   i-4e6f37a7293485e9b  running    10.9.1.128       yes
app                  i-7afb7ba745a956fcb  running    10.9.1.4         yes
```

For a real app with its databases, see
[`examples/stacks/multi-tier/`](../examples/stacks/multi-tier/stack.yaml): the
layout from [Multi-tier testing](multi-tier-testing.md) in one file.

## How it works

- `up` talks to the running server through the EC2 and IAM APIs, just like the
  AWS CLI does. A stack can use anything ec2-sim supports.
- Every resource gets the tags `ec2sim:stack=<name>` and `ec2sim:id=<kind>/<name>`.
  `up` looks for them first and creates only what's missing, so running it twice
  changes nothing. If you terminate an instance by hand, the next `up` replaces it
  at the same IP.
- Instances start in dependency order. Before it launches an instance, `up` waits
  until each instance in its `depends_on` passes its `ready` check. At the end it
  waits for every `ready` check, unless you pass `--no-wait`.
- `down` terminates the stack's instances. It then deletes its security groups,
  subnets and VPCs (with their Docker networks), and the key pairs and instance
  profiles listed in the file. AMIs are never touched.
- Stacks live as long as the server does. When the server stops, instances and
  VPCs go away, so run `up` again after a restart.

## Reference

### Top level

| Key | Required | Meaning |
|---|---|---|
| `name` | yes | Stack name: lowercase letters, digits and dashes. It's used in tags and resource names. |
| `region` | | Default `AWS_DEFAULT_REGION`, or `us-east-1` |
| `vpcs` | | VPCs and their subnets |
| `security_groups` | | Security groups and their inbound rules |
| `key_pairs` | | Key pairs to create or import |
| `instance_profiles` | | IAM instance profiles (each gets a role of the same name) |
| `instances` | | The instances |

`${VAR}` and `${VAR:-default}` in any string value come from the environment,
e.g. `ami: ${APP_AMI}`. A variable that isn't set and has no default is an error.

### `vpcs`

```yaml
vpcs:
  - name: main
    cidr: 10.7.0.0/16
    subnets:
      - {name: app, cidr: 10.7.1.0/24, az: us-west-2a}   # az defaults to <region>a
      - {name: data, cidr: 10.7.2.0/24}
```

Subnet names are stack-wide (instances refer to them by name alone), and each
subnet must be inside its VPC's CIDR. All instances in a VPC share one network,
so they reach each other across subnets, as in AWS.

### `security_groups`

```yaml
security_groups:
  - name: web
    vpc: main
    id: sg-138d3374            # optional: a fixed ID, for scripts that hard-code one
    description: web tier      # optional
    ingress:
      - {protocol: all, cidr: 10.7.0.0/16}       # all traffic from the VPC
      - {port: 443, cidr: [0.0.0.0/0]}           # tcp by default; cidr may be a list
      - {ports: 8000-8100, source: web}          # from members of a group (by name)
      - {protocol: udp, port: 53, cidr: 10.7.0.0/16}
```

`protocol` is `tcp` (default), `udp`, `icmp` or `all`. Each rule needs `cidr`,
`source`, or both. Rules are added when the group is created. Editing them later
in the file doesn't change an existing group, so run `reset` to apply changes.
As elsewhere in ec2-sim, rules control access through the
[HTTP gateway](networking.md). Traffic between instances isn't filtered.

### `key_pairs` and `instance_profiles`

```yaml
key_pairs:
  - me                                         # created; private key in ~/.ec2-sim/stacks/<stack>/me.pem
  - {name: ci, public_key_file: ~/.ssh/id_ed25519.pub}   # imported from your public key
instance_profiles: [app-services]
```

`down` deletes both, along with a saved private key.

### `instances`

```yaml
instances:
  - name: app                    # Name tag and DNS name inside the VPC
    ami: ubuntu-18.04            # see "AMIs" below
    type: t3.large               # default t3.micro
    subnet: app
    ip: 10.7.1.10                # optional fixed private IP
    count: 1                     # >1 starts app-1, app-2, …
    security_groups: [web]
    key: me
    profile: app-services
    user_data_file: boot.sh      # relative to the stack file; or user_data: | …
    tags: {Role: app}
    depends_on: [db]
    ready: {port: 8080, timeout: 300}
    forwards: [8080, {port: 22, local: 2222}]
```

**AMIs.** `ami` can be:

- an AMI ID: `ami-0123…`
- a base image by name: `amazonlinux-2023`, `ubuntu-24.04`, `ubuntu-18.04`, `ubuntu-26.04`
- the newest available AMI whose name matches a pattern: `{name: "my-app-*"}`,
  optionally with `owner:`. This is handy right after a Packer build.

**Readiness.** `ready` runs inside the instance, so it works on every OS and
in bastion mode:

- `{port: 27017}`: something accepts TCP connections on that port
- `{command: "curl -sf localhost:8080/health"}`: the command exits 0

`timeout` defaults to 300 seconds. On timeout, `up` stops and prints the
command that shows the instance's user-data log.

**Port forwards.** `forwards` publishes `localhost:<local>` on your computer
to the instance's port. That's the same as `ec2-sim forward`, and it works on
every host OS. `local` defaults to the same port number. Forwards aren't
supported with `count > 1`.

## Recipes

**Test a fresh AMI against its databases.** Bake with Packer, then pick it by name:

```yaml
  - name: app-1
    ami: {name: "staging-v2-app*"}
```

```bash
uv run ec2-sim reset stack.yaml     # new app instance from the newest AMI, fresh databases
```

**Recreate a production topology for a rollout script.** Give security groups
the IDs the script hard-codes (`id: sg-…`), create the key pairs and instance
profiles it expects, then run the script with
[`ec2-sim run`](../README.md#running-your-own-scripts-ec2-sim-run).

**CI.** Running `validate` needs no server or Docker. In a job with Docker, run
`up` (it exits non-zero if a readiness check times out), then your tests, then
`down`.
