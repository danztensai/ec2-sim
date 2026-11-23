// Typed bridge to the Rust side (Tauri commands) plus EC2 response parsing.
import { invoke } from "@tauri-apps/api/core";

export type Status = {
  running: boolean;
  managed: boolean;
  docker: boolean;
  network_mode: "routed" | "bastion";
  keys_dir: string;
  platform: "linux" | "macos" | "windows" | string;
  /** Running on Windows and driving ec2-sim inside WSL. */
  via_wsl: boolean;
};

export type Settings = {
  ec2sim_dir: string;
  endpoint: string;
  region: string;
  auto_start: boolean;
  extra_env: string;
  backend: "auto" | "wsl" | "native" | string;
  wsl_distro: string;
};

export type InstanceState = "pending" | "running" | "stopping" | "stopped" | "shutting-down" | "terminated";

export type Instance = {
  id: string;
  name: string;
  state: InstanceState;
  imageId: string;
  type: string;
  privateIp: string;
  publicIp: string;
  keyName: string;
  launchTime: string;
  az: string;
  tags: Record<string, string>;
  groups: { id: string; name: string }[];
};

export type Image = {
  id: string;
  name: string;
  description: string;
  state: string;
  architecture: string;
  created: string;
  ownerId: string;
  location: string;
  tags: Record<string, string>;
  /** Shipped with ec2-sim (Amazon Linux / Ubuntu base images). */
  base: boolean;
};

export type KeyPair = { name: string; fingerprint: string; localPath: string | null };

export type Forward = { name: string; instance_id: string; remote_port: number; local_port: number; bind: string; status: string };

type Json = any;
type Params = Record<string, string>;

export const api = {
  status: () => invoke<Status>("status"),
  startServer: () => invoke<void>("start_server"),
  stopServer: () => invoke<void>("stop_server"),
  serverLogs: () => invoke<string[]>("server_logs"),
  getSettings: () => invoke<Settings>("get_settings"),
  saveSettings: (s: Settings) => invoke<void>("save_settings", { new: s }),
  ec2: (action: string, params: Params = {}) => invoke<Json>("ec2", { action, params }),
  consoleOutput: (instanceId: string) => invoke<string>("console_output", { instanceId }),
  shellCommand: (instanceId: string) => invoke<string>("shell_command", { instanceId }),
  sshCommand: (ip: string, user: string, keyName: string, bastion: boolean) =>
    invoke<string>("ssh_command", { ip, user, keyName, bastion }),
  openTerminal: (command: string) => invoke<void>("open_terminal", { command }),
  saveKey: (name: string, material: string) => invoke<string>("save_key", { name, material }),
  keyPath: (name: string) => invoke<string | null>("key_path", { name }),
  deleteKeyFile: (name: string) => invoke<void>("delete_key_file", { name }),
  forwardAdd: (instanceId: string, remotePort: number, localPort: number) =>
    invoke<string>("forward_add", { instanceId, remotePort, localPort }),
  forwardList: () => invoke<Forward[]>("forward_list"),
  forwardRemove: (name: string) => invoke<void>("forward_remove", { name }),
  gatewayList: () => invoke<{ ports: number[]; bind: string; access?: string }>("gateway_list"),
  gatewaySetPort: (port: number, enabled: boolean) => invoke<void>("gateway_set_port", { port, enabled }),
  serverSource: (dir: string) => invoke<string>("server_source", { dir }),
};

// ------------------------------------------------------------------ helpers

const arr = (v: Json): Json[] => (Array.isArray(v) ? v : v ? [v] : []);

function tagMap(tagSet: Json): Record<string, string> {
  const out: Record<string, string> = {};
  for (const t of arr(tagSet)) if (t?.key) out[t.key] = t.value ?? "";
  return out;
}

/** "a.b.c" style list params: list("InstanceId", ["i-1"]) -> {"InstanceId.1": "i-1"} */
export function list(prefix: string, values: string[]): Params {
  return Object.fromEntries(values.map((v, i) => [`${prefix}.${i + 1}`, v]));
}

export function toBase64(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let bin = "";
  bytes.forEach((b) => (bin += String.fromCharCode(b)));
  return btoa(bin);
}

// ------------------------------------------------------------------ queries

export async function describeInstances(): Promise<Instance[]> {
  const r = await api.ec2("DescribeInstances");
  const out: Instance[] = [];
  for (const res of arr(r.reservationSet)) {
    for (const i of arr(res.instancesSet)) {
      const tags = tagMap(i.tagSet);
      out.push({
        id: i.instanceId,
        name: tags.Name ?? "",
        state: i.instanceState?.name ?? "pending",
        imageId: i.imageId ?? "",
        type: i.instanceType ?? "",
        privateIp: i.privateIpAddress ?? "",
        publicIp: i.ipAddress ?? "",
        keyName: i.keyName ?? "",
        launchTime: i.launchTime ?? "",
        az: i.placement?.availabilityZone ?? "",
        tags,
        groups: arr(i.groupSet).map((g) => ({ id: g.groupId, name: g.groupName ?? "" })),
      });
    }
  }
  return out.sort((a, b) => b.launchTime.localeCompare(a.launchTime));
}

function parseImage(i: Json, base: boolean): Image {
  return {
    id: i.imageId,
    name: i.name ?? "",
    description: i.description ?? "",
    state: i.imageState ?? "",
    architecture: i.architecture ?? "",
    created: i.creationDate ?? "",
    ownerId: i.imageOwnerId ?? "",
    location: i.imageLocation ?? "",
    tags: tagMap(i.tagSet),
    base,
  };
}

export async function describeImages(): Promise<Image[]> {
  const [mine, base] = await Promise.all([
    api.ec2("DescribeImages", list("Owner", ["self"])),
    api.ec2("DescribeImages", list("Owner", ["amazon", "099720109477"])),
  ]);
  // Base AMIs are the Docker-backed ones; moto's other stock AMIs can't boot containers.
  const isBase = (i: Json) => String(i.imageLocation ?? "").startsWith("docker:ec2sim/base-");
  const images = [
    ...arr(base.imagesSet).filter(isBase).map((i) => parseImage(i, true)),
    ...arr(mine.imagesSet).map((i) => parseImage(i, false)),
  ];
  const seen = new Set<string>();
  return images.filter((i) => (seen.has(i.id) ? false : (seen.add(i.id), true)));
}

export async function describeKeyPairs(): Promise<KeyPair[]> {
  const r = await api.ec2("DescribeKeyPairs");
  return Promise.all(
    arr(r.keySet).map(async (k) => ({
      name: k.keyName,
      fingerprint: k.keyFingerprint ?? "",
      localPath: await api.keyPath(k.keyName),
    })),
  );
}

// ------------------------------------------------------------------ actions

export async function createKeyPair(name: string): Promise<string> {
  const r = await api.ec2("CreateKeyPair", { KeyName: name });
  return api.saveKey(name, r.keyMaterial);
}

export async function runInstances(opts: {
  imageId: string;
  type: string;
  count: number;
  name: string;
  keyName: string;
  userData: string;
  securityGroupIds?: string[];
}): Promise<string[]> {
  const p: Params = {
    ImageId: opts.imageId,
    InstanceType: opts.type,
    MinCount: String(opts.count),
    MaxCount: String(opts.count),
  };
  if (opts.keyName) p.KeyName = opts.keyName;
  (opts.securityGroupIds ?? []).forEach((g, n) => (p[`SecurityGroupId.${n + 1}`] = g));
  if (opts.userData.trim()) p.UserData = toBase64(opts.userData);
  if (opts.name.trim()) {
    p["TagSpecification.1.ResourceType"] = "instance";
    p["TagSpecification.1.Tag.1.Key"] = "Name";
    p["TagSpecification.1.Tag.1.Value"] = opts.name.trim();
  }
  const r = await api.ec2("RunInstances", p);
  return arr(r.instancesSet).map((i) => i.instanceId);
}

export const instanceAction = (action: "StartInstances" | "StopInstances" | "RebootInstances" | "TerminateInstances", ids: string[]) =>
  api.ec2(action, list("InstanceId", ids));

export async function createImage(instanceId: string, name: string, description: string): Promise<string> {
  const r = await api.ec2("CreateImage", { InstanceId: instanceId, Name: name, Description: description });
  return r.imageId;
}

export const deregisterImage = (imageId: string) => api.ec2("DeregisterImage", { ImageId: imageId });

export const deleteKeyPair = async (name: string) => {
  await api.ec2("DeleteKeyPair", { KeyName: name });
  await api.deleteKeyFile(name);
};

// ------------------------------------------------------------------ display

/** Best guess of the SSH login user for an AMI. */
export function defaultUser(image: Image | undefined): string {
  const text = `${image?.name ?? ""} ${image?.description ?? ""}`.toLowerCase();
  return text.includes("ubuntu") ? "ubuntu" : "ec2-user";
}

export function friendlyImageName(image: Image | undefined, fallback = ""): string {
  if (!image) return fallback;
  const n = image.name.toLowerCase();
  if (image.base) {
    if (n.startsWith("al2023")) return "Amazon Linux 2023";
    if (n.includes("noble-24.04")) return "Ubuntu 24.04 LTS";
    if (n.includes("bionic-18.04")) return "Ubuntu 18.04 LTS (systemd)";
    if (n.includes("resolute-26.04")) return "Ubuntu 26.04 LTS (systemd)";
  }
  return image.name || image.id;
}

export function timeAgo(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

/** Host name the HTTP gateway knows an instance by (Name tag, else instance ID). */
export function webName(i: Instance): string {
  const n = i.name
    .toLowerCase()
    .replace(/[^a-z0-9.-]/g, "-")
    .replace(/^[-.]+|[-.]+$/g, "");
  return /^[a-z0-9][a-z0-9-]*$/.test(n) ? n : i.id;
}

/** 443 is TLS passthrough to the instance (its own certificate), so it is https. */
export const webUrl = (i: Instance, port: number) =>
  port === 443 ? `https://${webName(i)}.localhost/` : `http://${webName(i)}.localhost${port === 80 ? "" : `:${port}`}/`;

// ------------------------------------------------------------------ web access (security groups)

export type SgRule = { protocol: string; from: number | null; to: number | null; cidrs: string[] };
export type SecurityGroup = { id: string; name: string; rules: SgRule[] };

export async function describeSecurityGroups(ids: string[]): Promise<SecurityGroup[]> {
  if (!ids.length) return [];
  const r = await api.ec2("DescribeSecurityGroups", list("GroupId", ids));
  return arr(r.securityGroupInfo).map((g) => ({
    id: g.groupId,
    name: g.groupName ?? "",
    rules: arr(g.ipPermissions).map((p) => ({
      protocol: String(p.ipProtocol ?? ""),
      from: p.fromPort != null && p.fromPort !== "" ? Number(p.fromPort) : null,
      to: p.toPort != null && p.toPort !== "" ? Number(p.toPort) : null,
      cidrs: [...arr(p.ipRanges).map((x) => x.cidrIp), ...arr(p.ipv6Ranges).map((x) => x.cidrIpv6)].filter(Boolean),
    })),
  }));
}

/** Same rule as ec2-sim's gateway: TCP (or all traffic) covering the port, from an IP range. */
export function ruleAllows(rule: SgRule, port: number): boolean {
  if (!rule.cidrs.length) return false;
  if (rule.protocol === "-1" || rule.protocol === "all") return true;
  if (rule.protocol !== "tcp" && rule.protocol !== "6") return false;
  return rule.from != null && rule.to != null && rule.from <= port && port <= rule.to;
}

export const WEB_TAG = "ec2sim:web";

/** Why a port is (not) reachable through the gateway. */
export function portAccess(i: Instance, groups: SecurityGroup[], port: number, mode: string): { allowed: boolean; via: string } {
  if (mode === "open") return { allowed: true, via: "gateway access is open" };
  const tag = i.tags[WEB_TAG];
  if (tag !== undefined) {
    const t = tag.trim().toLowerCase();
    const ok = t === "all" || t === "*" || t.split(",").map((x) => x.trim()).includes(String(port));
    return { allowed: ok, via: `tag ${WEB_TAG}=${tag}` };
  }
  const g = groups.find((g) => g.rules.some((r) => ruleAllows(r, port)));
  return g ? { allowed: true, via: `security group ${g.name || g.id}` } : { allowed: false, via: "no security group allows it" };
}

const tcpPerm = (port: number): Params => ({
  "IpPermissions.1.IpProtocol": "tcp",
  "IpPermissions.1.FromPort": String(port),
  "IpPermissions.1.ToPort": String(port),
  "IpPermissions.1.IpRanges.1.CidrIp": "0.0.0.0/0",
});

export const allowPort = (groupId: string, port: number) => api.ec2("AuthorizeSecurityGroupIngress", { GroupId: groupId, ...tcpPerm(port) });
export const revokePort = (groupId: string, port: number) => api.ec2("RevokeSecurityGroupIngress", { GroupId: groupId, ...tcpPerm(port) });

/** A security group (default VPC) allowing TCP `ports` from anywhere; returns its ID. */
export async function createWebGroup(label: string, ports: number[]): Promise<string> {
  const name = `web-${(label || "instance").toLowerCase().replace(/[^a-z0-9-]/g, "-")}-${Date.now().toString(36)}`;
  const r = await api.ec2("CreateSecurityGroup", { GroupName: name, GroupDescription: `ec2-sim web access: ${ports.join(", ")}` });
  for (const p of ports) await allowPort(r.groupId, p);
  return r.groupId;
}
