import { useState } from "react";
import { Camera, Disc3, Rocket, Trash2 } from "lucide-react";
import { deregisterImage, friendlyImageName, Image, timeAgo } from "../api";
import { useStore } from "../store";
import { Button, Confirm, CopyText, Empty, Spinner, Tag, useAction } from "../ui";
import type { Page } from "../App";
import { useLaunch } from "./Launch";
import { CreateImageDialog } from "./InstanceDialogs";

export default function Images({ go }: { go: (p: Page) => void }) {
  const { images, status, loaded, refresh, instances } = useStore();
  const launch = useLaunch();
  const run = useAction();
  const [toDelete, setToDelete] = useState<Image | null>(null);
  const [creating, setCreating] = useState(false);

  if (!status?.running) return <Empty icon={<Disc3 size={28} />} title="Server is stopped" children="Start the server to see AMIs." />;
  if (!loaded) return <Spinner label="Loading AMIs…" />;

  const base = images.filter((i) => i.base);
  const mine = images.filter((i) => !i.base).sort((a, b) => b.created.localeCompare(a.created));
  const canBake = instances.some((i) => i.state === "running" || i.state === "stopped");

  return (
    <div className="page">
      <section>
        <div className="section-head">
          <div>
            <h2>Base AMIs</h2>
            <p className="muted">Ready-made images that ship with ec2-sim. Launch one, customise it, then bake your own AMI.</p>
          </div>
        </div>
        <div className="card-grid">
          {base.map((img) => (
            <div key={img.id} className="card ami-tile">
              <div className="ami-tile-head">
                <div className="ami-card-icon">
                  <Disc3 size={20} />
                </div>
                <div>
                  <div className="cell-title">{friendlyImageName(img)}</div>
                  <CopyText value={img.id} />
                </div>
              </div>
              <div className="ami-tile-meta">
                <Tag>{img.architecture}</Tag>
                {/systemd/i.test(img.description) && <Tag tone="info">systemd</Tag>}
              </div>
              <Button icon={<Rocket size={15} />} onClick={() => launch.open({ imageId: img.id })}>
                Launch
              </Button>
            </div>
          ))}
        </div>
      </section>

      <section>
        <div className="section-head">
          <div>
            <h2>Your AMIs</h2>
            <p className="muted">Created from your instances (or by Packer). They survive server restarts.</p>
          </div>
          <Button variant="primary" icon={<Camera size={16} />} disabled={!canBake} title={canBake ? "" : "Launch an instance first"} onClick={() => setCreating(true)}>
            Create AMI
          </Button>
        </div>
        {mine.length === 0 ? (
          <Empty
            icon={<Camera size={28} />}
            title="No AMIs of your own yet"
            action={
              <Button onClick={() => (canBake ? setCreating(true) : go("instances"))}>{canBake ? "Create one from an instance" : "Go to instances"}</Button>
            }
          >
            Launch an instance, install what you need, then use <b>Create AMI</b> to snapshot it.
          </Empty>
        ) : (
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Name</th>
                  <th>AMI ID</th>
                  <th>State</th>
                  <th>Arch</th>
                  <th>Created</th>
                  <th className="right">Actions</th>
                </tr>
              </thead>
              <tbody>
                {mine.map((img) => (
                  <tr key={img.id}>
                    <td>
                      <div className="cell-title">{img.name}</div>
                      {img.description && <div className="muted small">{img.description}</div>}
                    </td>
                    <td>
                      <CopyText value={img.id} />
                    </td>
                    <td>
                      <Tag tone={img.state === "available" ? "success" : img.state === "failed" ? "warn" : "neutral"}>{img.state}</Tag>
                    </td>
                    <td>{img.architecture}</td>
                    <td className="muted">{timeAgo(img.created)}</td>
                    <td className="right">
                      <div className="row-actions">
                        <Button size="sm" icon={<Rocket size={15} />} disabled={img.state !== "available"} onClick={() => launch.open({ imageId: img.id })}>
                          Launch
                        </Button>
                        <button className="icon-btn" title="Deregister" aria-label="Deregister" onClick={() => setToDelete(img)}>
                          <Trash2 size={17} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {creating && <CreateImageDialog onClose={() => setCreating(false)} />}
      {toDelete && (
        <Confirm
          danger
          title="Deregister AMI?"
          confirmLabel="Deregister"
          message={
            <>
              <b>{toDelete.name}</b> ({toDelete.id}) and its Docker image will be deleted. Running instances launched from it keep running.
            </>
          }
          onConfirm={async () => {
            await run(() => deregisterImage(toDelete.id), `Deregistered ${toDelete.id}`);
            await refresh();
          }}
          onClose={() => setToDelete(null)}
        />
      )}
    </div>
  );
}
