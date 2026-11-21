// Dialogs for one instance: Connect, Console output, Create AMI.
import { useEffect, useState } from "react";
import { Camera, ExternalLink, Plug, RefreshCw, SquareTerminal } from "lucide-react";
import { openUrl } from "@tauri-apps/plugin-opener";
import { allowPort, api, createImage, defaultUser, describeSecurityGroups, Instance, instanceAction, portAccess, revokePort, SecurityGroup, webName, webUrl } from "../api";
import { useStore } from "../store";
import { Button, CopyField, Field, Modal, Spinner, useToast } from "../ui";

export function ConnectDialog({ instance, onClose }: { instance: Instance; onClose: () => void }) {
  const { status, keys, imageById } = useStore();
  const toast = useToast();
  const [tab, setTab] = useState<"shell" | "ssh" | "web">("shell");
  const [shellCmd, setShellCmd] = useState("");
  const [user, setUser] = useState(defaultUser(imageById(instance.imageId)));
  const keyPath = keys.find((k) => k.name === instance.keyName)?.localPath;
  const bastion = status?.network_mode === "bastion";

  useEffect(() => {
    api.shellCommand(instance.id).then(setShellCmd).catch(() => {});
  }, [instance.id]);

  const ip = instance.publicIp || instance.privateIp;
  // Built in Rust so it fits where the terminal runs (e.g. `wsl.exe -- ssh ...` on Windows).
  const [sshCmd, setSshCmd] = useState("");
  useEffect(() => {
    if (!ip) return;
    api.sshCommand(ip, user, instance.keyName, bastion).then(setSshCmd).catch(() => setSshCmd(""));
  }, [ip, user, instance.keyName, bastion]);

  const open = async (cmd: string) => {
    try {
      await api.openTerminal(cmd);
    } catch (e) {
      toast("error", String(e));
    }
  };

  return (
    <Modal title={`Connect to ${instance.name || instance.id}`} subtitle={`${instance.id} · ${ip || "no IP yet"}`} onClose={onClose}>
      <div className="tabs">
        <button className={tab === "shell" ? "active" : ""} onClick={() => setTab("shell")}>
          Shell (no key needed)
        </button>
        <button className={tab === "ssh" ? "active" : ""} onClick={() => setTab("ssh")}>
          SSH
        </button>
        <button className={tab === "web" ? "active" : ""} onClick={() => setTab("web")}>
          Browser
        </button>
      </div>
      {tab === "web" ? (
        <WebAccess instance={instance} />
      ) : tab === "shell" ? (
        <div className="stack">
          <p className="muted">
            Opens a root shell inside the instance with <code>docker exec</code>. Fastest way in; no key pair or network needed.
            {status?.via_wsl && " Runs through WSL, where ec2-sim and Docker live."}
          </p>
          {shellCmd && <CopyField value={shellCmd} label="Command" />}
          <div>
            <Button variant="primary" icon={<SquareTerminal size={16} />} onClick={() => open(shellCmd)} disabled={!shellCmd}>
              Open in terminal
            </Button>
          </div>
        </div>
      ) : (
        <div className="stack">
          <p className="muted">
            Real SSH, like on EC2. Use this to test key pairs, sshd config, or tools such as Ansible.
            {bastion && " Docker Desktop detected: the connection goes through ec2-sim's bastion (password: ec2sim)."}
          </p>
          {!instance.keyName && <div className="callout warn">This instance was launched without a key pair, so SSH login won't work. Use Shell instead.</div>}
          {instance.keyName && !keyPath && (
            <div className="callout warn">
              No local private key for <b>{instance.keyName}</b> in ~/.ec2-sim/keys. Add <code>-i path/to/key.pem</code> yourself.
            </div>
          )}
          <Field label="Login user">
            <select value={user} onChange={(e) => setUser(e.target.value)}>
              <option>ec2-user</option>
              <option>ubuntu</option>
              <option>root</option>
            </select>
          </Field>
          {sshCmd && <CopyField value={sshCmd} label="Command" />}
          {status?.via_wsl && <p className="muted small">Instance IPs are only reachable inside WSL, so SSH runs there (keys are in WSL's ~/.ec2-sim/keys).</p>}
          <div>
            <Button variant="primary" icon={<SquareTerminal size={16} />} onClick={() => open(sshCmd)} disabled={!ip || !sshCmd}>
              Open in terminal
            </Button>
          </div>
        </div>
      )}
    </Modal>
  );
}

export function ConsoleDialog({ instance, onClose }: { instance: Instance; onClose: () => void }) {
  const [text, setText] = useState<string | null>(null);
  const [error, setError] = useState("");
  const load = () => {
    setError("");
    api.consoleOutput(instance.id).then(setText).catch((e) => setError(String(e)));
  };
  useEffect(load, [instance.id]);
  return (
    <Modal
      wide
      title={`Console: ${instance.name || instance.id}`}
      subtitle="User data output and the instance's boot log"
      onClose={onClose}
      footer={
        <Button icon={<RefreshCw size={16} />} onClick={load}>
          Refresh
        </Button>
      }
    >
      {error ? <div className="callout error">{error}</div> : text === null ? <Spinner label="Reading…" /> : <pre className="console">{text}</pre>}
    </Modal>
  );
}

export function CreateImageDialog({ instance, onClose, onCreated }: { instance?: Instance; onClose: () => void; onCreated?: (id: string) => void }) {
  const { instances, refresh } = useStore();
  const toast = useToast();
  const candidates = instances.filter((i) => i.state === "running" || i.state === "stopped");
  const [instanceId, setInstanceId] = useState(instance?.id ?? candidates[0]?.id ?? "");
  const selected = instances.find((i) => i.id === instanceId);
  const stamp = new Date().toISOString().slice(0, 16).replace(/[-:T]/g, "");
  const [name, setName] = useState(`${selected?.name || "my-ami"}-${stamp}`);
  const [description, setDescription] = useState("");
  const [stopFirst, setStopFirst] = useState(false);
  const [busy, setBusy] = useState(false);

  async function create() {
    setBusy(true);
    try {
      if (stopFirst && selected?.state === "running") await instanceAction("StopInstances", [instanceId]);
      const id = await createImage(instanceId, name, description);
      toast("success", `AMI ${id} created from ${instanceId}`);
      await refresh();
      onCreated?.(id);
      onClose();
    } catch (e) {
      toast("error", String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title="Create AMI"
      subtitle="Snapshots the instance's filesystem into a new AMI you can launch again."
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" icon={<Camera size={16} />} busy={busy} disabled={!instanceId || !name.trim()} onClick={create}>
            Create AMI
          </Button>
        </>
      }
    >
      <div className="stack">
        {!instance && (
          <Field label="From instance">
            <select value={instanceId} onChange={(e) => setInstanceId(e.target.value)}>
              {candidates.length === 0 && <option value="">No running or stopped instances</option>}
              {candidates.map((i) => (
                <option key={i.id} value={i.id}>
                  {i.name ? `${i.name} (${i.id})` : i.id} · {i.state}
                </option>
              ))}
            </select>
          </Field>
        )}
        <Field label="AMI name">
          <input value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <Field label="Description" hint="Optional">
          <input value={description} onChange={(e) => setDescription(e.target.value)} placeholder="What's baked in?" />
        </Field>
        <label className="check">
          <input type="checkbox" checked={stopFirst} onChange={(e) => setStopFirst(e.target.checked)} />
          Stop the instance first (what Packer does, for a consistent snapshot)
        </label>
      </div>
    </Modal>
  );
}

const PORT_PRESETS: { label: string; port: number; web?: boolean }[] = [
  { label: "SSH", port: 22 },
  { label: "HTTP", port: 80, web: true },
  { label: "HTTPS", port: 443, web: true },
  { label: "Postgres", port: 5432 },
  { label: "MySQL", port: 3306 },
  { label: "MongoDB", port: 27017 },
  { label: "Redis", port: 6379 },
  { label: "App 3000", port: 3000, web: true },
  { label: "App 8080", port: 8080, web: true },
];

const defaultLocal = (p: number) => (p < 1024 ? p + 10000 : p);

export function ForwardDialog({ instance, onClose, onCreated }: { instance: Instance; onClose: () => void; onCreated?: () => void }) {
  const toast = useToast();
  const [remote, setRemote] = useState(80);
  const [local, setLocal] = useState(defaultLocal(80));
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<{ local: number; remote: number } | null>(null);
  const web = PORT_PRESETS.find((p) => p.port === remote)?.web ?? (remote === 8000 || remote === 5000);
  const valid = remote > 0 && remote < 65536 && local > 0 && local < 65536;

  async function create() {
    setBusy(true);
    try {
      await api.forwardAdd(instance.id, remote, local);
      setDone({ local, remote });
      onCreated?.();
    } catch (e) {
      toast("error", String(e));
    } finally {
      setBusy(false);
    }
  }

  const url = done ? `${web && done.remote === 443 ? "https" : "http"}://localhost:${done.local}` : "";
  return (
    <Modal
      title={`Port forward: ${instance.name || instance.id}`}
      subtitle="Reach a port on this instance from your computer (Windows, WSL, macOS or Linux) at localhost."
      onClose={onClose}
      footer={
        done ? (
          <Button variant="primary" onClick={onClose}>
            Done
          </Button>
        ) : (
          <>
            <Button variant="ghost" onClick={onClose}>
              Cancel
            </Button>
            <Button variant="primary" icon={<Plug size={16} />} busy={busy} disabled={!valid} onClick={create}>
              Create forward
            </Button>
          </>
        )
      }
    >
      {done ? (
        <div className="stack">
          <div className="callout info">
            <b>localhost:{done.local}</b> now reaches <b>{instance.name || instance.id}</b> port {done.remote}. It stays until you remove it or
            terminate the instance.
          </div>
          <CopyField value={`localhost:${done.local}`} label="Address on your computer" />
          {done.remote === 22 && <CopyField value={`ssh -p ${done.local} -i <key.pem> <user>@localhost`} label="SSH" />}
          {web && (
            <div>
              <Button icon={<ExternalLink size={16} />} onClick={() => openUrl(url)}>
                Open {url}
              </Button>
            </div>
          )}
        </div>
      ) : (
        <div className="stack">
          <Field label="Instance port">
            <div className="template-row">
              {PORT_PRESETS.map((p) => (
                <button
                  key={p.port}
                  type="button"
                  className={`chip ${remote === p.port ? "active" : ""}`}
                  onClick={() => {
                    setRemote(p.port);
                    setLocal(defaultLocal(p.port));
                  }}
                >
                  {p.label} · {p.port}
                </button>
              ))}
            </div>
          </Field>
          <div className="grid-2">
            <Field label="Instance port (custom)">
              <input
                type="number"
                min={1}
                max={65535}
                value={remote}
                onChange={(e) => {
                  const v = Number(e.target.value);
                  setRemote(v);
                  setLocal(defaultLocal(v));
                }}
              />
            </Field>
            <Field label="Port on your computer" hint="Ports below 1024 default to +10000">
              <input type="number" min={1} max={65535} value={local} onChange={(e) => setLocal(Number(e.target.value))} />
            </Field>
          </div>
        </div>
      )}
    </Modal>
  );
}

/** http://<name>.localhost[:port] links through ec2-sim's HTTP gateway. */
function WebAccess({ instance }: { instance: Instance }) {
  const toast = useToast();
  const { instances, refresh } = useStore();
  const [ports, setPorts] = useState<number[] | null>(null);
  const [mode, setMode] = useState("security-groups");
  const [groups, setGroups] = useState<SecurityGroup[]>([]);
  const [newPort, setNewPort] = useState("");
  const [busy, setBusy] = useState(false);
  const load = async () => {
    try {
      const g = await api.gatewayList();
      setPorts(g.ports);
      setMode(g.access ?? "security-groups");
    } catch {
      setPorts([]);
    }
    setGroups(await describeSecurityGroups(instance.groups.map((g) => g.id)).catch(() => []));
  };
  useEffect(() => {
    load();
  }, []);
  const target = instance.groups[0];
  const shared = target ? instances.filter((i) => i.id !== instance.id && i.state !== "terminated" && i.groups.some((g) => g.id === target.id)).length : 0;

  async function change(port: number, allow: boolean) {
    if (!target) return;
    setBusy(true);
    try {
      await (allow ? allowPort(target.id, port) : revokePort(target.id, port));
      toast("success", `${allow ? "Allowed" : "Revoked"} TCP ${port} in ${target.name || target.id}`);
      await load();
      await refresh();
    } catch (e) {
      toast("error", String(e));
    } finally {
      setBusy(false);
    }
  }
  const named = webName(instance) !== instance.id;

  async function addPort() {
    const p = Number(newPort);
    if (!(p > 0 && p < 65536)) return;
    setBusy(true);
    try {
      await api.gatewaySetPort(p, true);
      setNewPort("");
      await load();
    } catch (e) {
      toast("error", String(e));
    } finally {
      setBusy(false);
    }
  }

  if (ports === null) return <Spinner label="Checking the HTTP gateway…" />;
  return (
    <div className="stack">
      <p className="muted">
        Open web apps on this instance from your browser by name. <code>*.localhost</code> resolves to your own computer in browsers and curl
        (Windows, macOS, Linux); ec2-sim's gateway routes it to the instance{named ? " by its Name tag" : ""}.
      </p>
      {!named && <div className="callout info">Give the instance a Name tag (e.g. app1) for a friendlier address; the instance ID works too.</div>}
      {ports.length === 0 && <div className="callout warn">The HTTP gateway isn't running (restart the server, or host port 80 may be busy). Add a port below.</div>}
      {ports.map((p) => {
        const a = portAccess(instance, groups, p, mode);
        return (
          <div key={p} className="web-port">
            <div className="web-row">
              <CopyField value={webUrl(instance, p)} label={`Instance port ${p}`} />
              <Button icon={<ExternalLink size={16} />} disabled={!a.allowed} onClick={() => openUrl(webUrl(instance, p))}>
                Open
              </Button>
            </div>
            <div className={`access ${a.allowed ? "ok" : "no"}`}>
              {a.allowed ? "✅ Allowed" : "🚫 Blocked"} <span className="muted">— {a.via}</span>
              {mode !== "open" && instance.tags["ec2sim:web"] === undefined && target && (
                <Button size="sm" variant="ghost" busy={busy} onClick={() => change(p, !a.allowed)}>
                  {a.allowed ? "Revoke" : `Allow TCP ${p} in ${target.name || target.id}`}
                </Button>
              )}
            </div>
          </div>
        );
      })}
      {mode !== "open" && shared > 0 && (
        <div className="muted small">
          Allow/Revoke edits security group <b>{target?.name || target?.id}</b>, which {shared} other instance{shared > 1 ? "s" : ""} also use.
        </div>
      )}
      <div className="grid-2">
        <Field label="Another port" hint="Adds a gateway port for every instance (e.g. 9404 → http://name.localhost:9404)">
          <input type="number" min={1} max={65535} value={newPort} onChange={(e) => setNewPort(e.target.value)} placeholder="9404" />
        </Field>
        <div style={{ alignSelf: "center" }}>
          <Button icon={<Plug size={16} />} busy={busy} disabled={!newPort} onClick={addPort}>
            Add port
          </Button>
        </div>
      </div>
      <p className="muted small">The app must listen on 0.0.0.0 (not only 127.0.0.1) inside the instance. For databases and other non-HTTP ports use ⋯ → Port forward.</p>
    </div>
  );
}
