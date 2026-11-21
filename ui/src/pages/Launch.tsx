// "Launch instance" wizard, available from every page via useLaunch().
import { createContext, ReactNode, useContext, useMemo, useState } from "react";
import { Disc3, KeyRound, Rocket } from "lucide-react";
import { createKeyPair, createWebGroup, friendlyImageName, Image, runInstances } from "../api";
import { useStore } from "../store";
import { Button, Field, Modal, Tag, useToast } from "../ui";

const INSTANCE_TYPES = ["t3.micro", "t3.small", "t3.medium", "t3.large", "t2.large", "m5.large", "c5.large"];

const USER_DATA_TEMPLATES: { label: string; body: string }[] = [
  { label: "None", body: "" },
  {
    label: "Hello world",
    body: `#!/bin/bash
echo "Hello from $(hostname) at $(date)" > /tmp/hello.txt`,
  },
  {
    label: "Install nginx (Amazon Linux)",
    body: `#!/bin/bash
dnf -y install nginx
echo "<h1>Hello from ec2-sim</h1>" > /usr/share/nginx/html/index.html
nginx`,
  },
  {
    label: "Install nginx (Ubuntu)",
    body: `#!/bin/bash
apt-get update && apt-get install -y nginx
echo "<h1>Hello from ec2-sim</h1>" > /var/www/html/index.html
(systemctl start nginx 2>/dev/null || nginx)`,
  },
  {
    label: "Show instance metadata",
    body: `#!/bin/bash
TOKEN=$(curl -s -X PUT -H "X-aws-ec2-metadata-token-ttl-seconds: 300" http://169.254.169.254/latest/api/token)
curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/dynamic/instance-identity/document`,
  },
];

type Ctx = { open: (opts?: { imageId?: string }) => void };
const LaunchCtx = createContext<Ctx>({ open: () => {} });
export const useLaunch = () => useContext(LaunchCtx);

export function LaunchProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<{ imageId?: string } | null>(null);
  return (
    <LaunchCtx.Provider value={{ open: (o) => setState(o ?? {}) }}>
      {children}
      {state && <LaunchModal initialImage={state.imageId} onClose={() => setState(null)} />}
    </LaunchCtx.Provider>
  );
}

function AmiCard({ image, selected, onSelect }: { image: Image; selected: boolean; onSelect: () => void }) {
  return (
    <button type="button" className={`ami-card ${selected ? "selected" : ""}`} onClick={onSelect}>
      <div className="ami-card-icon">
        <Disc3 size={20} />
      </div>
      <div className="ami-card-body">
        <div className="ami-card-title">{friendlyImageName(image)}</div>
        <div className="ami-card-sub" title={`${image.id} · ${image.architecture}`}>
          {image.id}
        </div>
      </div>
      {image.base ? <Tag tone="info">Base</Tag> : <Tag tone="success">Yours</Tag>}
    </button>
  );
}

function LaunchModal({ initialImage, onClose }: { initialImage?: string; onClose: () => void }) {
  const { images, keys, refresh, status } = useStore();
  const toast = useToast();
  const usable = useMemo(() => images.filter((i) => i.state === "available"), [images]);
  const [imageId, setImageId] = useState(initialImage ?? usable[0]?.id ?? "");
  const [name, setName] = useState("");
  const [type, setType] = useState("t3.micro");
  const [count, setCount] = useState(1);
  const [keyName, setKeyName] = useState(keys[0]?.name ?? "");
  const [newKey, setNewKey] = useState("");
  const [userData, setUserData] = useState("");
  const [webPorts, setWebPorts] = useState<number[]>([80]);
  const [customPort, setCustomPort] = useState("");
  const [busy, setBusy] = useState(false);

  const creatingKey = keyName === "__new__";
  const canLaunch = !!imageId && count >= 1 && count <= 10 && (!creatingKey || /^[\w.@-]+$/.test(newKey));

  async function launch() {
    setBusy(true);
    try {
      let key = keyName;
      if (creatingKey) {
        const path = await createKeyPair(newKey);
        toast("success", `Key pair saved to ${path}`);
        key = newKey;
      }
      // Web access = a security group allowing those ports (like AWS); the gateway follows it.
      const securityGroupIds = webPorts.length ? [await createWebGroup(name, webPorts)] : undefined;
      const ids = await runInstances({ imageId, type, count, name, keyName: key, userData, securityGroupIds });
      toast("success", `Launched ${ids.join(", ")}`);
      await refresh();
      onClose();
    } catch (e) {
      toast("error", String(e));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      wide
      title="Launch instance"
      subtitle="Starts a container that behaves like an EC2 instance: SSH, user data, metadata service."
      onClose={onClose}
      footer={
        <>
          <span className="muted foot-note">
            {status?.running ? "First launch of a base AMI can take a minute while its image builds." : "Start the server first."}
          </span>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" icon={<Rocket size={16} />} busy={busy} disabled={!canLaunch || !status?.running} onClick={launch}>
            Launch{count > 1 ? ` ${count}` : ""}
          </Button>
        </>
      }
    >
      <section className="wizard-step">
        <h3>
          <span className="step-num">1</span> Choose an AMI
        </h3>
        {usable.length === 0 ? (
          <p className="muted">No AMIs yet. Is the server running?</p>
        ) : (
          <div className="ami-grid">
            {usable.map((img) => (
              <AmiCard key={img.id} image={img} selected={img.id === imageId} onSelect={() => setImageId(img.id)} />
            ))}
          </div>
        )}
      </section>

      <section className="wizard-step">
        <h3>
          <span className="step-num">2</span> Details
        </h3>
        <div className="grid-3">
          <Field label="Name" hint="Optional, shown in the list">
            <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. web-1" autoFocus />
          </Field>
          <Field label="Instance type" hint="Label only: containers share your machine">
            <select value={type} onChange={(e) => setType(e.target.value)}>
              {INSTANCE_TYPES.map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          </Field>
          <Field label="How many">
            <input type="number" min={1} max={10} value={count} onChange={(e) => setCount(Number(e.target.value))} />
          </Field>
        </div>
      </section>

      <section className="wizard-step">
        <h3>
          <span className="step-num">3</span> Access
        </h3>
        <div className="grid-2">
          <Field label="Key pair" hint="For SSH. You can always open a shell without a key.">
            <select value={keyName} onChange={(e) => setKeyName(e.target.value)}>
              <option value="">No key pair</option>
              {keys.map((k) => (
                <option key={k.name} value={k.name}>
                  {k.name}
                  {k.localPath ? "" : " (no local .pem)"}
                </option>
              ))}
              <option value="__new__">+ Create a new key pair…</option>
            </select>
          </Field>
          {creatingKey && (
            <Field label="New key pair name" hint="Saved as ~/.ec2-sim/keys/<name>.pem">
              <div className="input-icon">
                <KeyRound size={16} />
                <input value={newKey} onChange={(e) => setNewKey(e.target.value)} placeholder="my-key" />
              </div>
            </Field>
          )}
        </div>
      </section>

      <section className="wizard-step">
        <h3>
          <span className="step-num">4</span> Web access <span className="muted small">(open in your browser as http://{(name || "name").toLowerCase().replace(/[^a-z0-9-]/g, "-")}.localhost)</span>
        </h3>
        <div className="template-row">
          {[80, 443, 8080, 3000, ...webPorts.filter((p) => ![80, 443, 8080, 3000].includes(p))].map((p) => (
            <button
              key={p}
              type="button"
              className={`chip ${webPorts.includes(p) ? "active" : ""}`}
              onClick={() => setWebPorts((w) => (w.includes(p) ? w.filter((x) => x !== p) : [...w, p].sort((a, b) => a - b)))}
            >
              {webPorts.includes(p) ? "✓ " : ""}
              {p}
            </button>
          ))}
          <input
            className="chip-input"
            type="number"
            min={1}
            max={65535}
            placeholder="other port"
            value={customPort}
            onChange={(e) => setCustomPort(e.target.value)}
            onKeyDown={(e) => {
              const v = Number(customPort);
              if (e.key === "Enter" && v > 0 && v < 65536) {
                setWebPorts((w) => [...new Set([...w, v])].sort((a, b) => a - b));
                setCustomPort("");
              }
            }}
          />
        </div>
        <span className="field-hint">
          {webPorts.length
            ? `Creates a security group allowing TCP ${webPorts.join(", ")}. Ports other than 80 also need adding to the gateway (Connect → Browser).`
            : "None: the instance gets the default security group and isn't reachable through the gateway (port forwards still work)."}
        </span>
      </section>

      <section className="wizard-step">
        <h3>
          <span className="step-num">5</span> User data <span className="muted small">(optional script run on first boot)</span>
        </h3>
        <div className="template-row">
          {USER_DATA_TEMPLATES.map((t) => (
            <button key={t.label} type="button" className={`chip ${userData === t.body ? "active" : ""}`} onClick={() => setUserData(t.body)}>
              {t.label}
            </button>
          ))}
        </div>
        <textarea className="code" rows={6} value={userData} onChange={(e) => setUserData(e.target.value)} placeholder="#!/bin/bash&#10;echo hello" spellCheck={false} />
      </section>
    </Modal>
  );
}
