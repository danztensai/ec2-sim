# Getting started

This guide takes you from nothing to a baked AMI in about ten minutes:
install, start the server, launch an instance, connect, customise it, bake it,
and launch a copy.

## 1. Install

You need Docker, [uv](https://docs.astral.sh/uv/) (it installs Python 3.12 for
you) and, for the examples, the AWS CLI.

| Your machine | Docker to use | Notes |
|---|---|---|
| Linux | Docker Engine | Everything works directly. |
| Windows | Docker Engine **inside WSL2** (Ubuntu) | Run ec2-sim inside WSL. The [desktop app](desktop-app.md) runs natively on Windows and drives it there. |
| macOS | Docker Desktop or OrbStack | Docker Desktop uses [bastion mode](../README.md#macos-and-docker-desktop) for SSH, which is detected automatically. |

```bash
git clone https://github.com/danztensai/ec2-sim.git
cd ec2-sim
uv sync
uv run ec2-sim build-images    # optional: build the base AMIs now (about 2 to 5 min, once)
```

## 2. Start the server

```bash
uv run ec2-sim
```

It listens on `http://127.0.0.1:4566` and logs every action. Leave it running.
Ctrl+C stops it and removes all instance containers; AMIs you bake are kept.

In a second terminal, point the AWS CLI at it. Use fake credentials only:
ec2-sim doesn't check them, and they guarantee nothing reaches real AWS.

```bash
export AWS_ACCESS_KEY_ID=test AWS_SECRET_ACCESS_KEY=test AWS_DEFAULT_REGION=us-east-1
alias ec2='aws --endpoint-url http://localhost:4566 ec2'
```

## 3. Launch an instance

```bash
ec2 create-key-pair --key-name me --query KeyMaterial --output text > me.pem && chmod 600 me.pem

cat > web.sh <<'EOF'
#!/bin/bash
dnf install -y nginx
systemctl enable --now nginx || nginx
echo "hello from $(hostname)" > /usr/share/nginx/html/index.html
EOF

ec2 run-instances --image-id ami-0ec2511a120230001 --instance-type t3.micro \
  --key-name me --user-data file://web.sh \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=web-1}]'
```

`ami-0ec2511a120230001` is Amazon Linux 2023. For the full list, see
[Base AMIs](../README.md#base-amis). Use a **systemd** base AMI (Ubuntu 18.04 or
26.04) when your provisioning uses `systemctl`.

## 4. Connect

```bash
ec2 describe-instances --query 'Reservations[].Instances[].[InstanceId,Tags[0].Value,State.Name,PrivateIpAddress]' --output table
ssh -i me.pem ec2-user@172.31.0.4                # SSH, like a real instance
docker exec -it ec2sim-i-0123456789abcdef0 bash   # or a root shell, no key needed
```

User data output is in `/var/log/cloud-init-output.log` inside the instance.

## 5. Open it in your browser

The HTTP gateway serves each instance as `http://<Name>.localhost/`, but only on
ports its security groups open, as on AWS. Open port 80 in the default group:

```bash
ec2 authorize-security-group-ingress --group-name default --protocol tcp --port 80 --cidr 0.0.0.0/0
curl http://web-1.localhost/       # hello from ip-172-31-0-4
```

`*.localhost` resolves to your own machine in browsers and curl, including on
Windows when ec2-sim runs in WSL. For more, see [Networking](networking.md).

## 6. Bake an AMI and launch a copy

```bash
ec2 stop-instances --instance-ids i-0123456789abcdef0
ec2 create-image --instance-id i-0123456789abcdef0 --name web-golden-v1
ec2 describe-images --owners self --query 'Images[].[ImageId,Name,State]'   # pending, then available
ec2 run-instances --image-id ami-<new> --instance-type t3.micro --key-name me \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=web-2}]'
curl http://web-2.localhost/
```

`CreateImage` returns at once with state `pending` and bakes in the background,
the same as on AWS. The AMI is a Docker image (`ec2sim/ami:<ami-id>`) and is
still there after a server restart.

## Next

- Do the same in the GUI: [Desktop app](desktop-app.md)
- Build AMIs with Packer or your existing pipeline: [Baking AMIs](baking-amis.md)
- Run an app with its databases: [Multi-tier testing](multi-tier-testing.md)
