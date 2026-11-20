// Dialogs for one instance: Connect, Console output, Create AMI.
import { useEffect, useMemo, useState } from "react";
import { Camera, RefreshCw, SquareTerminal } from "lucide-react";
import { api, createImage, defaultUser, Instance, instanceAction } from "../api";
import { useStore } from "../store";
import { Button, CopyField, Field, Modal, Spinner, useToast } from "../ui";

export function ConnectDialog({ instance, onClose }: { instance: Instance; onClose: () => void }) {
  const { status, keys, imageById } = useStore();
  const toast = useToast();
  const [tab, setTab] = useState<"shell" | "ssh">("shell");
  const [shellCmd, setShellCmd] = useState("");
  const [user, setUser] = useState(defaultUser(imageById(instance.imageId)));
  const keyPath = keys.find((k) => k.name === instance.keyName)?.localPath;
  const bastion = status?.network_mode === "bastion";

  useEffect(() => {
    api.shellCommand(instance.id).then(setShellCmd).catch(() => {});
  }, [instance.id]);

  const ip = instance.publicIp || instance.privateIp;
  const sshCmd = useMemo(() => {
    const parts = ["ssh"];
    if (keyPath) parts.push("-i", JSON.stringify(keyPath));
    if (bastion) parts.push("-J", "ec2sim@127.0.0.1:2222");
    parts.push("-o", "StrictHostKeyChecking=no", `${user}@${ip}`);
    return parts.join(" ");
  }, [keyPath, bastion, user, ip]);

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
      </div>
      {tab === "shell" ? (
        <div className="stack">
          <p className="muted">Opens a root shell inside the instance with <code>docker exec</code>. Fastest way in; no key pair or network needed.</p>
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
          <CopyField value={sshCmd} label="Command" />
          <div>
            <Button variant="primary" icon={<SquareTerminal size={16} />} onClick={() => open(sshCmd)} disabled={!ip}>
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
