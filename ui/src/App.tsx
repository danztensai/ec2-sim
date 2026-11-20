import { useState } from "react";
import { Disc3, Gauge, KeyRound, Power, ScrollText, Server, Settings as SettingsIcon } from "lucide-react";
import { StoreProvider, useStore } from "./store";
import { Button, ToastProvider, useAction } from "./ui";
import Overview from "./pages/Overview";
import Instances from "./pages/Instances";
import Images from "./pages/Images";
import KeyPairs from "./pages/KeyPairs";
import Logs from "./pages/Logs";
import SettingsPage from "./pages/Settings";
import { LaunchProvider } from "./pages/Launch";

export type Page = "overview" | "instances" | "images" | "keys" | "logs" | "settings";

const NAV: { id: Page; label: string; icon: React.ReactNode }[] = [
  { id: "overview", label: "Overview", icon: <Gauge size={18} /> },
  { id: "instances", label: "Instances", icon: <Server size={18} /> },
  { id: "images", label: "AMIs", icon: <Disc3 size={18} /> },
  { id: "keys", label: "Key pairs", icon: <KeyRound size={18} /> },
  { id: "logs", label: "Server log", icon: <ScrollText size={18} /> },
  { id: "settings", label: "Settings", icon: <SettingsIcon size={18} /> },
];

function ServerControl() {
  const { status, starting, startServer, stopServer } = useStore();
  const run = useAction();
  const [stopping, setStopping] = useState(false);
  const running = status?.running;
  const label = starting ? "Starting…" : stopping ? "Stopping…" : running ? "Running" : "Stopped";
  return (
    <div className="server-control">
      <span className={`pill ${running ? "pill-on" : "pill-off"}`} title={running && !status?.managed ? "Started outside this app" : undefined}>
        <span className="dot" />
        Server {label}
        {running && !status?.managed && <em> · external</em>}
      </span>
      {running ? (
        status?.managed && (
          <Button
            size="sm"
            variant="ghost"
            icon={<Power size={15} />}
            busy={stopping}
            onClick={async () => {
              setStopping(true);
              await run(stopServer, "Server stopped, instances cleaned up");
              setStopping(false);
            }}
          >
            Stop
          </Button>
        )
      ) : (
        <Button size="sm" variant="primary" icon={<Power size={15} />} busy={starting} onClick={() => run(startServer, "Server started")}>
          Start
        </Button>
      )}
    </div>
  );
}

function Shell() {
  const [page, setPage] = useState<Page>("overview");
  const { instances, images, keys, settings, status } = useStore();
  const counts: Partial<Record<Page, number>> = {
    instances: instances.filter((i) => i.state !== "terminated").length,
    images: images.filter((i) => !i.base).length,
    keys: keys.length,
  };
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark">e2</div>
          <div>
            <div className="brand-name">ec2-sim</div>
            <div className="brand-sub">Local EC2 on Docker</div>
          </div>
        </div>
        <nav>
          {NAV.map((n) => (
            <button key={n.id} className={`nav-item ${page === n.id ? "active" : ""}`} onClick={() => setPage(n.id)}>
              {n.icon}
              <span>{n.label}</span>
              {counts[n.id] ? <span className="count">{counts[n.id]}</span> : null}
            </button>
          ))}
        </nav>
        <div className="sidebar-foot muted">
          <div>Region: {settings?.region ?? "…"}</div>
          {status?.network_mode === "bastion" && <div title="Docker Desktop: SSH goes through 127.0.0.1:2222">SSH via bastion :2222</div>}
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <h1>{NAV.find((n) => n.id === page)?.label}</h1>
          <ServerControl />
        </header>
        <div className="content">
          {page === "overview" && <Overview go={setPage} />}
          {page === "instances" && <Instances />}
          {page === "images" && <Images go={setPage} />}
          {page === "keys" && <KeyPairs />}
          {page === "logs" && <Logs />}
          {page === "settings" && <SettingsPage />}
        </div>
      </main>
    </div>
  );
}

export default function App() {
  return (
    <ToastProvider>
      <StoreProvider>
        <LaunchProvider>
          <Shell />
        </LaunchProvider>
      </StoreProvider>
    </ToastProvider>
  );
}
