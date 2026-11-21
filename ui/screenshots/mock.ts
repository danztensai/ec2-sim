// Demo backend for documentation screenshots: answers the app's Tauri commands
// with fixed, realistic data (shaped like ec2.rs' XML->JSON output), so the
// screenshots never show real instances, keys or secrets.
// Used only by screenshots.html (`npm run screenshots`), never by the app.
import { mockIPC, mockWindows } from "@tauri-apps/api/mocks";
import { emit } from "@tauri-apps/api/event";

const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString();
const tags = (t: Record<string, string>) => Object.entries(t).map(([key, value]) => ({ key, value }));

const GROUPS: Record<string, { groupId: string; groupName: string; ipPermissions: unknown[] }> = {
  "sg-0a1b2c3d4e5f60001": {
    groupId: "sg-0a1b2c3d4e5f60001",
    groupName: "web",
    ipPermissions: [
      { ipProtocol: "tcp", fromPort: "80", toPort: "80", ipRanges: [{ cidrIp: "0.0.0.0/0" }] },
      { ipProtocol: "tcp", fromPort: "22", toPort: "22", ipRanges: [{ cidrIp: "0.0.0.0/0" }] },
    ],
  },
  "sg-0a1b2c3d4e5f60002": {
    groupId: "sg-0a1b2c3d4e5f60002",
    groupName: "db-internal",
    ipPermissions: [{ ipProtocol: "tcp", fromPort: "5432", toPort: "5432", ipRanges: [{ cidrIp: "172.31.0.0/16" }] }],
  },
};

function instance(id: string, name: string, state: string, image: string, type: string, ip: string, min: number, group: string, extra: Record<string, string> = {}) {
  const g = GROUPS[group];
  return {
    instanceId: id,
    imageId: image,
    instanceState: { name: state },
    privateIpAddress: ip,
    keyName: "demo-key",
    launchTime: ago(min),
    instanceType: type,
    placement: { availabilityZone: "us-east-1a" },
    tagSet: tags({ Name: name, ...extra }),
    groupSet: [{ groupId: g.groupId, groupName: g.groupName }],
  };
}

const INSTANCES = [
  instance("i-0c4f9e2a7b1d30a51", "web-1", "running", "ami-5d2a8c41e07b9f3a2", "t3.small", "172.31.0.4", 6, "sg-0a1b2c3d4e5f60001"),
  instance("i-07b3e1d9c2a84f612", "web-2", "running", "ami-5d2a8c41e07b9f3a2", "t3.small", "172.31.0.5", 6, "sg-0a1b2c3d4e5f60001"),
  instance("i-0e8a2f6c1b7d94c03", "db-1", "running", "ami-0ec2511b240400001", "t3.medium", "172.31.0.6", 14, "sg-0a1b2c3d4e5f60002"),
  instance("i-0a9d4b7e3c2f15e84", "packer-builder", "stopped", "ami-0ec2511a120230001", "t3.micro", "172.31.0.3", 42, "sg-0a1b2c3d4e5f60001"),
];

const base = (id: string, name: string, description: string, location: string, owner: string) => ({
  imageId: id, name, description, imageState: "available", architecture: "x86_64", creationDate: "2026-01-01T00:00:00.000Z",
  imageOwnerId: owner, imageLocation: `docker:ec2sim/${location}:latest`, tagSet: [],
});
const BASE_IMAGES = [
  base("ami-0ec2511a120230001", "al2023-ami-2023.ec2sim-kernel-6.1-x86_64", "Amazon Linux 2023 (ec2-sim container AMI)", "base-amazonlinux-2023", "137112412989"),
  base("ami-0ec2511b240400001", "ubuntu/images/hvm-ssd-gp3/ubuntu-noble-24.04-amd64-server-ec2sim", "Canonical, Ubuntu, 24.04 LTS (ec2-sim container AMI)", "base-ubuntu-24.04", "099720109477"),
  base("ami-0ec2511b180400001", "ubuntu/images/hvm-ssd/ubuntu-bionic-18.04-amd64-server-ec2sim", "Canonical, Ubuntu, 18.04 LTS with systemd (ec2-sim container AMI)", "base-ubuntu-18.04", "099720109477"),
  base("ami-0ec2511b260400001", "ubuntu/images/hvm-ssd-gp3/ubuntu-resolute-26.04-amd64-server-ec2sim", "Canonical, Ubuntu, 26.04 LTS with systemd (ec2-sim container AMI)", "base-ubuntu-26.04", "099720109477"),
];
const MY_IMAGES = [
  { imageId: "ami-5d2a8c41e07b9f3a2", name: "web-golden-20261122.0912", description: "nginx + app, baked by Packer", imageState: "available", architecture: "x86_64",
    creationDate: ago(25), imageOwnerId: "123456789012", imageLocation: "123456789012/web-golden-20261122.0912", tagSet: tags({ Role: "web" }) },
  { imageId: "ami-81f3c0b7d24e6a915", name: "web-golden-20261122.1040", description: "nginx + app, baked by Packer", imageState: "pending", architecture: "x86_64",
    creationDate: ago(1), imageOwnerId: "123456789012", imageLocation: "123456789012/web-golden-20261122.1040", tagSet: tags({ Role: "web" }) },
];

function ec2(action: string, params: Record<string, string>) {
  switch (action) {
    case "DescribeInstances":
      return { reservationSet: [{ instancesSet: INSTANCES }] };
    case "DescribeImages":
      return { imagesSet: params["Owner.1"] === "self" ? MY_IMAGES : BASE_IMAGES };
    case "DescribeKeyPairs":
      return { keySet: [{ keyName: "demo-key", keyFingerprint: "3e:9b:1c:52:a4:7f:0d:e6:88:21:4b:c9:5a:f0:13:7d" }] };
    case "DescribeSecurityGroups":
      return { securityGroupInfo: Object.values(params).map((id) => GROUPS[id]).filter(Boolean) };
    default:
      return {};
  }
}

const SERVER_LOG = [
  "ec2-sim starting: Docker 27.5.1, network mode routed",
  "Base AMIs: al2023 ubuntu-24.04 ubuntu-18.04 ubuntu-26.04",
  "Restored 2 AMI(s) from Docker images",
  "IMDS listening on 169.254.169.254 (v1 + v2)",
  "HTTP gateway on 127.0.0.1:80,443 (access: security-groups)",
  "HTTPS proxy for real AWS hostnames on :4567",
  "EC2 API on http://localhost:4566",
  "RunInstances i-0e8a2f6c1b7d94c03 ami-0ec2511b240400001 -> ec2sim-i-0e8a2f6c1b7d94c03 (172.31.0.6)",
  "RunInstances i-0c4f9e2a7b1d30a51 ami-5d2a8c41e07b9f3a2 -> ec2sim-i-0c4f9e2a7b1d30a51 (172.31.0.4)",
  "RunInstances i-07b3e1d9c2a84f612 ami-5d2a8c41e07b9f3a2 -> ec2sim-i-07b3e1d9c2a84f612 (172.31.0.5)",
  "gateway: web-1.localhost:80 web-2.localhost:80 (security group web)",
  "CreateImage ami-81f3c0b7d24e6a915 from i-0a9d4b7e3c2f15e84: pending (baking in background)",
];

const CONSOLE = `[    0.000000] ec2-sim: booting i-0c4f9e2a7b1d30a51 (ami-5d2a8c41e07b9f3a2)
ec2sim-init: hostname ip-172-31-0-4
ec2sim-init: instance metadata at http://169.254.169.254/latest/
ec2sim-init: authorized key demo-key for ec2-user
ec2sim-init: starting sshd
ec2sim-userdata: running user data (#!/bin/bash, 3 lines)
+ systemctl enable --now nginx
Created symlink /etc/systemd/system/multi-user.target.wants/nginx.service.
+ echo ok
ok
ec2sim-userdata: done (exit 0)
Cloud-init v. ec2-sim finished. Up 4.2 seconds`;

export const RUN_OUTPUT = [
  "[ec2-sim run] runner ec2sim-run-7f3a (Packer 1.16.2, AWS CLI 2) in /work/infra",
  "[ec2-sim run] AWS calls go to ec2-sim; only fake credentials are set",
  "==> amazon-ebs.web: Prevalidating AMI Name: web-golden-20261122.1040",
  "    amazon-ebs.web: Found Image ID: ami-0ec2511a120230001",
  "==> amazon-ebs.web: Creating temporary keypair: packer_6740b2c1",
  "==> amazon-ebs.web: Launching a source AWS instance...",
  "    amazon-ebs.web: Instance ID: i-0a9d4b7e3c2f15e84",
  "==> amazon-ebs.web: Waiting for SSH to become available...",
  "==> amazon-ebs.web: Connected to SSH!",
  "==> amazon-ebs.web: Provisioning with shell script: scripts/install-web.sh",
  "    amazon-ebs.web: Installing nginx 1.26 ...",
  "    amazon-ebs.web: Complete!",
  "==> amazon-ebs.web: Stopping the source instance...",
  "==> amazon-ebs.web: Creating AMI web-golden-20261122.1040 from instance i-0a9d4b7e3c2f15e84",
  "    amazon-ebs.web: AMI: ami-81f3c0b7d24e6a915",
  "==> amazon-ebs.web: Waiting for AMI to become ready...",
];

export function installMock() {
  mockWindows("main");
  mockIPC(
    (cmd, args: any) => {
      switch (cmd) {
        case "status":
          return { running: true, managed: true, docker: true, network_mode: "routed", keys_dir: "~/.ec2-sim/keys", platform: "windows", via_wsl: true };
        case "get_settings":
          return { ec2sim_dir: "/home/me/ec2-sim", endpoint: "http://localhost:4566", region: "us-east-1", auto_start: true, extra_env: "", backend: "auto", wsl_distro: "" };
        case "ec2":
          return ec2(args.action, args.params ?? {});
        case "key_path":
          return "~/.ec2-sim/keys/demo-key.pem";
        case "server_logs":
          return SERVER_LOG;
        case "console_output":
          return CONSOLE;
        case "shell_command":
          return `docker exec -it ec2sim-${args.instanceId} bash -l`;
        case "ssh_command":
          return `ssh -i ~/.ec2-sim/keys/${args.keyName}.pem ${args.user}@${args.ip}`;
        case "forward_list":
          return [{ name: "ec2sim-fwd-db-1-5432", instance_id: "i-0e8a2f6c1b7d94c03", remote_port: 5432, local_port: 15432, bind: "127.0.0.1", status: "running" }];
        case "gateway_list":
          return { ports: [80, 443], bind: "127.0.0.1", access: "security-groups" };
        case "script_running":
          return false;
        default:
          return null;
      }
    },
    { shouldMockEvents: true },
  );
}

export const emitRunOutput = async () => {
  for (const l of RUN_OUTPUT) await emit("run-output", l);
};
