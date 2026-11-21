import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Camera, MoreHorizontal, Play, Plug, Plus, RotateCw, ScrollText, Search, Server, Square, SquareTerminal, Trash2, X } from "lucide-react";
import { api, Forward, friendlyImageName, Instance, instanceAction, timeAgo } from "../api";
import { useStore } from "../store";
import { Button, Confirm, CopyText, Empty, Spinner, StateBadge, useAction } from "../ui";
import { useLaunch } from "./Launch";
import { ConnectDialog, ConsoleDialog, CreateImageDialog, ForwardDialog } from "./InstanceDialogs";

type Dialog = { kind: "connect" | "console" | "image" | "terminate" | "forward"; instance: Instance } | null;

function RowMenu({ instance, onPick }: { instance: Instance; onPick: (k: "reboot" | "image" | "terminate" | "forward") => void }) {
  // Fixed-position menu (portal) so it is never clipped by the table's scroll area.
  const [pos, setPos] = useState<{ top: number; right: number } | null>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const menu = useRef<HTMLDivElement>(null);
  const open = pos !== null;
  const setOpen = (o: boolean | ((x: boolean) => boolean)) => {
    const next = typeof o === "function" ? o(open) : o;
    if (!next || !btn.current) return setPos(null);
    const r = btn.current.getBoundingClientRect();
    const below = window.innerHeight - r.bottom > 190;
    setPos({ top: below ? r.bottom + 4 : r.top - 4 - 172, right: window.innerWidth - r.right });
  };
  useEffect(() => {
    if (!open) return;
    const close = (e: Event) => {
      const t = e.target as Node;
      if (menu.current?.contains(t) || btn.current?.contains(t)) return;
      setPos(null);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [open]);
  const alive = instance.state !== "terminated" && instance.state !== "shutting-down";
  return (
    <>
      <button ref={btn} className="icon-btn" aria-label="More actions" title="More actions" aria-expanded={open} onClick={() => setOpen((o) => !o)} disabled={!alive}>
        <MoreHorizontal size={18} />
      </button>
      {open &&
        createPortal(
        <div className="menu" role="menu" ref={menu} style={{ position: "fixed", top: pos.top, right: pos.right }}>
          <button disabled={instance.state !== "running"} onClick={() => (setOpen(false), onPick("reboot"))}>
            <RotateCw size={15} /> Reboot
          </button>
          <button disabled={instance.state !== "running"} onClick={() => (setOpen(false), onPick("forward"))}>
            <Plug size={15} /> Port forward…
          </button>
          <button onClick={() => (setOpen(false), onPick("image"))}>
            <Camera size={15} /> Create AMI from this
          </button>
          <div className="menu-sep" />
          <button className="danger" onClick={() => (setOpen(false), onPick("terminate"))}>
            <Trash2 size={15} /> Terminate
          </button>
        </div>,
          document.body,
        )}
    </>
  );
}

export default function Instances() {
  const { instances, status, loaded, imageById, refresh } = useStore();
  const launch = useLaunch();
  const run = useAction();
  const [query, setQuery] = useState("");
  const [showTerminated, setShowTerminated] = useState(false);
  const [dialog, setDialog] = useState<Dialog>(null);
  const [pending, setPending] = useState<Record<string, boolean>>({});
  const [forwards, setForwards] = useState<Forward[]>([]);
  const loadForwards = () => api.forwardList().then(setForwards).catch(() => {});
  useEffect(() => {
    if (!status?.running) return;
    loadForwards();
    const t = setInterval(loadForwards, 6000);
    return () => clearInterval(t);
  }, [status?.running]);

  const act = async (i: Instance, action: Parameters<typeof instanceAction>[0], msg: string) => {
    setPending((p) => ({ ...p, [i.id]: true }));
    await run(() => instanceAction(action, [i.id]), msg);
    await refresh();
    setPending((p) => ({ ...p, [i.id]: false }));
  };

  if (!status?.running) {
    return <Empty icon={<Server size={28} />} title="Server is stopped" children="Start the server (top right) to see and launch instances." />;
  }
  if (!loaded) return <Spinner label="Loading instances…" />;

  const q = query.toLowerCase();
  const terminatedCount = instances.filter((i) => i.state === "terminated").length;
  const rows = instances.filter(
    (i) =>
      (showTerminated || i.state !== "terminated") &&
      (!q || [i.id, i.name, i.privateIp, i.publicIp, i.imageId, i.type].some((v) => v.toLowerCase().includes(q))),
  );

  return (
    <div className="page">
      <div className="toolbar">
        <div className="input-icon search">
          <Search size={16} />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search by name, ID, IP, AMI…" />
        </div>
        {terminatedCount > 0 && (
          <label className="check">
            <input type="checkbox" checked={showTerminated} onChange={(e) => setShowTerminated(e.target.checked)} />
            Show terminated ({terminatedCount})
          </label>
        )}
        <div className="spacer" />
        <Button variant="primary" icon={<Plus size={16} />} onClick={() => launch.open()}>
          Launch instance
        </Button>
      </div>

      {forwards.length > 0 && (
        <div className="card forwards">
          <div className="forwards-head">
            <Plug size={16} /> <b>Port forwards</b> <span className="muted small">reachable from this computer</span>
          </div>
          {forwards.map((f) => {
            const inst = instances.find((x) => x.id === f.instance_id);
            return (
              <div key={f.name} className="forward-row">
                <CopyText value={`localhost:${f.local_port}`} />
                <span className="muted">→</span>
                <span>
                  {inst?.name || f.instance_id} <span className="muted">port {f.remote_port}</span>
                </span>
                {f.status !== "running" && <span className="tag tag-warn">{f.status}</span>}
                <div className="spacer" />
                <button
                  className="icon-btn"
                  title="Remove forward"
                  aria-label="Remove forward"
                  onClick={async () => {
                    await run(() => api.forwardRemove(f.name), `Removed localhost:${f.local_port}`);
                    loadForwards();
                  }}
                >
                  <X size={16} />
                </button>
              </div>
            );
          })}
        </div>
      )}

      {rows.length === 0 ? (
        <Empty
          icon={<Server size={28} />}
          title={instances.length ? "No matching instances" : "No instances yet"}
          action={
            !instances.length && (
              <Button variant="primary" icon={<Plus size={16} />} onClick={() => launch.open()}>
                Launch your first instance
              </Button>
            )
          }
        >
          {!instances.length && "An instance is a container you can SSH into, run user data on, and bake into an AMI."}
        </Empty>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>State</th>
                <th>AMI</th>
                <th>IP address</th>
                <th>Type</th>
                <th className="right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((i) => {
                const img = imageById(i.imageId);
                const busy = pending[i.id];
                const dead = i.state === "terminated" || i.state === "shutting-down";
                return (
                  <tr key={i.id} className={dead ? "row-dim" : ""}>
                    <td>
                      <div className="cell-title">{i.name || <span className="muted">unnamed</span>}</div>
                      <CopyText value={i.id} />
                    </td>
                    <td>
                      <StateBadge state={i.state} />
                    </td>
                    <td>
                      <div className="cell-title">{friendlyImageName(img, i.imageId)}</div>
                      {img && <CopyText value={i.imageId} />}
                    </td>
                    <td>{i.publicIp || i.privateIp ? <CopyText value={i.publicIp || i.privateIp} /> : <span className="muted">—</span>}</td>
                    <td>
                      <div>{i.type}</div>
                      <div className="muted small" title={i.launchTime}>
                        {timeAgo(i.launchTime)}
                      </div>
                    </td>
                    <td className="right">
                      <div className="row-actions">
                        <Button size="sm" icon={<SquareTerminal size={15} />} disabled={i.state !== "running"} onClick={() => setDialog({ kind: "connect", instance: i })}>
                          Connect
                        </Button>
                        <button className="icon-btn" title="Console output" aria-label="Console output" disabled={dead} onClick={() => setDialog({ kind: "console", instance: i })}>
                          <ScrollText size={17} />
                        </button>
                        {i.state === "stopped" ? (
                          <button className="icon-btn" title="Start" aria-label="Start" disabled={busy} onClick={() => act(i, "StartInstances", `Starting ${i.id}`)}>
                            <Play size={17} />
                          </button>
                        ) : (
                          <button className="icon-btn" title="Stop" aria-label="Stop" disabled={busy || i.state !== "running"} onClick={() => act(i, "StopInstances", `Stopping ${i.id}`)}>
                            <Square size={16} />
                          </button>
                        )}
                        <RowMenu
                          instance={i}
                          onPick={(k) =>
                            k === "reboot" ? act(i, "RebootInstances", `Rebooting ${i.id}`) : setDialog({ kind: k, instance: i })
                          }
                        />
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}

      {dialog?.kind === "connect" && <ConnectDialog instance={dialog.instance} onClose={() => setDialog(null)} />}
      {dialog?.kind === "console" && <ConsoleDialog instance={dialog.instance} onClose={() => setDialog(null)} />}
      {dialog?.kind === "image" && <CreateImageDialog instance={dialog.instance} onClose={() => setDialog(null)} />}
      {dialog?.kind === "forward" && <ForwardDialog instance={dialog.instance} onClose={() => setDialog(null)} onCreated={loadForwards} />}
      {dialog?.kind === "terminate" && (
        <Confirm
          danger
          title="Terminate instance?"
          confirmLabel="Terminate"
          message={
            <>
              <b>{dialog.instance.name || dialog.instance.id}</b> and its container will be deleted. Anything not baked into an AMI is lost.
            </>
          }
          onConfirm={() => act(dialog.instance, "TerminateInstances", `Terminated ${dialog.instance.id}`)}
          onClose={() => setDialog(null)}
        />
      )}
    </div>
  );
}
