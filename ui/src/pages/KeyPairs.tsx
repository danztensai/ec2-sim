import { useState } from "react";
import { KeyRound, Plus, Trash2 } from "lucide-react";
import { createKeyPair, deleteKeyPair, KeyPair } from "../api";
import { useStore } from "../store";
import { Button, Confirm, CopyText, Empty, Field, Modal, useAction, useToast } from "../ui";

function CreateKeyDialog({ onClose }: { onClose: () => void }) {
  const { refresh } = useStore();
  const toast = useToast();
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const valid = /^[\w.@-]+$/.test(name) && !name.startsWith(".");
  return (
    <Modal
      title="Create key pair"
      subtitle="The private key is saved on this computer so Connect → SSH can use it."
      onClose={onClose}
      footer={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            busy={busy}
            disabled={!valid}
            onClick={async () => {
              setBusy(true);
              try {
                const path = await createKeyPair(name);
                toast("success", `Saved private key to ${path}`);
                await refresh();
                onClose();
              } catch (e) {
                toast("error", String(e));
              } finally {
                setBusy(false);
              }
            }}
          >
            Create
          </Button>
        </>
      }
    >
      <Field label="Name" hint="Letters, digits, - _ . @  ·  saved as ~/.ec2-sim/keys/<name>.pem (mode 600)">
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="my-key" autoFocus />
      </Field>
    </Modal>
  );
}

export default function KeyPairs() {
  const { keys, status, refresh } = useStore();
  const run = useAction();
  const [creating, setCreating] = useState(false);
  const [toDelete, setToDelete] = useState<KeyPair | null>(null);

  if (!status?.running) return <Empty icon={<KeyRound size={28} />} title="Server is stopped" children="Start the server to manage key pairs." />;

  return (
    <div className="page">
      <div className="toolbar">
        <p className="muted">Key pairs put a public key on instances at launch so you can SSH in. They live in server memory, so recreate them after a restart.</p>
        <div className="spacer" />
        <Button variant="primary" icon={<Plus size={16} />} onClick={() => setCreating(true)}>
          Create key pair
        </Button>
      </div>
      {keys.length === 0 ? (
        <Empty icon={<KeyRound size={28} />} title="No key pairs" action={<Button onClick={() => setCreating(true)}>Create one</Button>}>
          You don't need one to get a shell (use Connect → Shell), only for real SSH.
        </Empty>
      ) : (
        <div className="table-wrap">
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Private key on this computer</th>
                <th>Fingerprint</th>
                <th className="right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {keys.map((k) => (
                <tr key={k.name}>
                  <td className="cell-title">{k.name}</td>
                  <td>{k.localPath ? <CopyText value={k.localPath} /> : <span className="muted">not on this computer</span>}</td>
                  <td className="mono small muted">{k.fingerprint}</td>
                  <td className="right">
                    <button className="icon-btn" title="Delete" aria-label="Delete" onClick={() => setToDelete(k)}>
                      <Trash2 size={17} />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {creating && <CreateKeyDialog onClose={() => setCreating(false)} />}
      {toDelete && (
        <Confirm
          danger
          title="Delete key pair?"
          confirmLabel="Delete"
          message={
            <>
              Deletes <b>{toDelete.name}</b> from ec2-sim{toDelete.localPath ? <> and its private key file <code>{toDelete.localPath}</code></> : null}. Instances already
              launched with it keep the public key.
            </>
          }
          onConfirm={async () => {
            await run(() => deleteKeyPair(toDelete.name), `Deleted ${toDelete.name}`);
            await refresh();
          }}
          onClose={() => setToDelete(null)}
        />
      )}
    </div>
  );
}
