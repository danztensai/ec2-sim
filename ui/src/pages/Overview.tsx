import { useState } from "react";
import { ArrowRight, Camera, CheckCircle2, CircleAlert, Disc3, KeyRound, Power, Rocket, Server, Wrench } from "lucide-react";
import { friendlyImageName } from "../api";
import { useStore } from "../store";
import { Button, StateBadge, useAction } from "../ui";
import type { Page } from "../App";
import { useLaunch } from "./Launch";
import { CreateImageDialog } from "./InstanceDialogs";

function Stat({ label, value, icon, onClick }: { label: string; value: number | string; icon: React.ReactNode; onClick?: () => void }) {
  return (
    <button className="card stat" onClick={onClick} disabled={!onClick}>
      <div className="stat-icon">{icon}</div>
      <div>
        <div className="stat-value">{value}</div>
        <div className="stat-label">{label}</div>
      </div>
    </button>
  );
}

function Check({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return (
    <li className={ok ? "ok" : "bad"}>
      {ok ? <CheckCircle2 size={18} /> : <CircleAlert size={18} />}
      <span>{children}</span>
    </li>
  );
}

export default function Overview({ go }: { go: (p: Page) => void }) {
  const { status, settings, instances, images, keys, starting, startServer, imageById } = useStore();
  const launch = useLaunch();
  const run = useAction();
  const [baking, setBaking] = useState(false);

  if (!status?.running) {
    return (
      <div className="page">
        <div className="hero card">
          <div className="hero-icon">
            <Power size={28} />
          </div>
          <h2>Start the simulator</h2>
          <p className="muted">ec2-sim runs a local EC2 API where instances are Docker containers and AMIs are Docker images. Nothing touches AWS.</p>
          <Button size="lg" variant="primary" icon={<Power size={18} />} busy={starting} disabled={!status?.docker} onClick={() => run(startServer, "Server started")}>
            {starting ? "Starting…" : "Start server"}
          </Button>
          <ul className="checks">
            <Check ok={!!status?.docker}>Docker is {status?.docker ? "running" : "not reachable: start Docker Desktop / the Docker daemon"}</Check>
            <Check ok>
              ec2-sim: <code>{settings?.ec2sim_dir || "installed automatically with uv"}</code>{" "}
              <button className="link" onClick={() => go("settings")}>
                change
              </button>
            </Check>
          </ul>
          <p className="muted small">The first start installs ec2-sim (if no checkout is set) and builds the base images, which takes a few minutes; later starts take seconds.</p>
        </div>
      </div>
    );
  }

  const live = instances.filter((i) => i.state !== "terminated");
  const running = live.filter((i) => i.state === "running").length;
  const mine = images.filter((i) => !i.base);

  return (
    <div className="page">
      <div className="stats">
        <Stat label="Running instances" value={running} icon={<Server size={20} />} onClick={() => go("instances")} />
        <Stat label="Stopped" value={live.filter((i) => i.state === "stopped").length} icon={<Server size={20} />} onClick={() => go("instances")} />
        <Stat label="Your AMIs" value={mine.length} icon={<Disc3 size={20} />} onClick={() => go("images")} />
        <Stat label="Key pairs" value={keys.length} icon={<KeyRound size={20} />} onClick={() => go("keys")} />
      </div>

      <section className="card flow">
        <h2>Bake an AMI in three steps</h2>
        <div className="flow-steps">
          <div className="flow-step">
            <div className="flow-num">1</div>
            <div>
              <h3>Launch</h3>
              <p className="muted">Start an instance from a base AMI, with optional user data.</p>
              <Button variant="primary" icon={<Rocket size={16} />} onClick={() => launch.open()}>
                Launch instance
              </Button>
            </div>
          </div>
          <ArrowRight className="flow-arrow" size={20} />
          <div className="flow-step">
            <div className="flow-num">2</div>
            <div>
              <h3>Customise</h3>
              <p className="muted">Connect and install packages, config, services: whatever the AMI needs.</p>
              <Button icon={<Wrench size={16} />} disabled={!running} onClick={() => go("instances")}>
                Connect to an instance
              </Button>
            </div>
          </div>
          <ArrowRight className="flow-arrow" size={20} />
          <div className="flow-step">
            <div className="flow-num">3</div>
            <div>
              <h3>Create AMI</h3>
              <p className="muted">Snapshot it into a reusable AMI, then launch copies from it.</p>
              <Button icon={<Camera size={16} />} disabled={!live.length} onClick={() => setBaking(true)}>
                Create AMI
              </Button>
            </div>
          </div>
        </div>
      </section>

      <section className="card">
        <div className="section-head">
          <h2>Recent instances</h2>
          <button className="link" onClick={() => go("instances")}>
            View all
          </button>
        </div>
        {live.length === 0 ? (
          <p className="muted">No instances yet. Launch one to get started.</p>
        ) : (
          <ul className="recent">
            {live.slice(0, 5).map((i) => (
              <li key={i.id}>
                <div>
                  <div className="cell-title">{i.name || i.id}</div>
                  <div className="muted small">
                    {friendlyImageName(imageById(i.imageId), i.imageId)} · {i.publicIp || i.privateIp || "no IP yet"}
                  </div>
                </div>
                <StateBadge state={i.state} />
              </li>
            ))}
          </ul>
        )}
      </section>

      {status.network_mode === "bastion" && (
        <div className="callout info">
          Docker Desktop detected. Instance IPs aren't reachable from your Mac/PC directly, so SSH goes through ec2-sim's bastion at <code>127.0.0.1:2222</code>.
          Connect → SSH builds the right command for you.
        </div>
      )}

      {baking && <CreateImageDialog onClose={() => setBaking(false)} onCreated={() => go("images")} />}
    </div>
  );
}
