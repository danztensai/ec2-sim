# Why ec2-sim, and what it's for

## What it is

ec2-sim is a local stand-in for Amazon EC2. It speaks the real EC2 API, so the
AWS CLI, SDKs, Packer and shell scripts talk to it as if it were AWS. Behind
the API, instances are real Docker containers and AMIs are real Docker images:

- `RunInstances` starts a container you can SSH into, with your key pair and user data.
- Your provisioning (apt, packages, systemd services, config files) runs for real inside it.
- `CreateImage` snapshots that container into an AMI you can launch again.
- Auto Scaling groups, load balancer health checks and the instance metadata
  service (169.254.169.254) are there too, so boot scripts and rollout scripts work.

Everything runs on a developer machine or CI runner. No AWS account, credentials
or network access to AWS is needed.

## What problem it solves

Infrastructure scripts (AMI builds, bootstrap/user-data, rollout scripts) are
usually only testable against real AWS. In practice that means:

- **Slow feedback.** Every change needs a real build: launch an instance, wait
  for SSH, provision, snapshot, clean up. A typo found at minute 11 costs another 11 minutes.
- **Access and risk.** Testing needs AWS credentials and permissions near
  staging or production. A rollout script under test can touch live Auto
  Scaling groups and load balancers.
- **Leftovers.** Failed runs leave instances, AMIs, snapshots, launch
  configurations and security groups behind, and someone has to find and delete them.
- **Untested paths.** Error handling and rollback paths rarely get exercised,
  because forcing failures in real AWS is awkward and risky.

ec2-sim moves that loop onto your laptop: same scripts, same commands, no AWS.

## What it's good for

| Use | Example |
|---|---|
| Developing AMI builds | Iterate on a Packer template or provisioning script; inspect the instance with `docker exec` while it builds. |
| Testing boot-time behaviour | User data, systemd units, `ec2metadata` / IMDS lookups, secrets agents that start at boot. |
| Rehearsing rollouts | Blue/green ASG cutover scripts against seeded ASGs and a load balancer, with no live traffic. |
| CI checks | Run the AMI build on every pull request and fail fast, without CI holding AWS credentials. |
| Onboarding and training | New engineers can run the real pipeline end to end without AWS access. |
| Reproducing bugs | Build a known-bad AMI locally and debug it with a shell inside. |

## What it does not replace

ec2-sim runs containers, not virtual machines. Do a final check on real AWS
before production for anything that depends on:

- the kernel, kernel modules, or a custom kernel
- real network rules (security groups aren't enforced) and VPC routing
- real IAM/STS (instance-profile credentials are simulated)
- EBS volume behaviour, instance-type performance, or Windows

Treat it like unit tests for infrastructure: it catches most mistakes early and
cheaply, and a final run on AWS confirms the rest.

## Will it save money?

**Directly, a little. Indirectly, more.** The AWS bill for test builds is
usually small; the bigger costs are engineer time, mistakes reaching
staging or production, and forgotten resources.

### Direct AWS cost per test run (approximate us-west-2 on-demand prices; check current pricing)

| Item | Approximate cost |
|---|---|
| One AMI build: a t2.large for ~15 min | ~$0.02–0.03 |
| Each AMI kept: 16 GB EBS snapshot | ~$0.80 / month until deleted |
| Rollout rehearsal: 3 + 3 t2.large for 1 hour, plus a classic ELB | ~$0.60 |
| A forgotten t2.large left running | ~$68 / month |
| Forgotten AMIs/snapshots from repeated test builds (e.g. 50) | ~$40 / month |

Ten test builds a day for a month costs only a few dollars in compute. Most of
the AWS bill from testing comes from **leftovers**: snapshots, AMIs and
instances nobody cleaned up. ec2-sim produces none of them.

### Indirect savings (usually larger)

- **Engineer time.** A failed real build means waiting on AWS, then cleaning up. Locally
  there's no AWS wait, no cleanup, and you can open a shell in the instance mid-build. If
  an engineer saves an hour a week, that's worth far more than the compute bill.
- **Incidents avoided.** A broken AMI or rollout script that reaches production
  costs downtime, rollback effort and customer impact. Catching it locally is the cheapest place.
- **Less access needed.** Testing doesn't need AWS credentials, which shrinks
  who needs production-adjacent permissions and the risk of accidents.
- **No license cost.** Comparable local EC2 emulation with real instances is a
  paid feature in commercial tools (for example LocalStack Pro). ec2-sim is
  built on open-source moto (Apache-2.0) and Docker.

### Example

Pointed at an existing Packer + Auto Scaling pipeline, unmodified, ec2-sim
found two real bugs in one 11-minute local run, with no AWS access:

1. Application services required an environment file that the new secrets
   agent wrote under a different name. Package installs failed and the AMI build stopped.
2. The rollout script printed "Done." and exited successfully after the build
   failed. In deploy mode it would have created a launch configuration with an
   empty AMI ID.

On AWS, finding these takes at least one failed real build per bug, plus
cleanup, and the second bug could have reached a production rollout.

## Costs of using it

- A Linux/WSL2 machine with Docker, and a few GB of disk for base images.
- Some setup for an existing pipeline: alias its source AMI ID and seed the
  resources it expects (IAM profiles, security groups, ASGs). That's done once per pipeline.
- Maintenance: base images and the moto dependency need occasional updates.

## Summary

Use ec2-sim to develop and test EC2 infrastructure code quickly and safely, and
keep real AWS for the final check. Compute savings are modest. The real
return is faster iteration, fewer resources left behind, less need for AWS access,
and bugs caught before they reach staging or production.
