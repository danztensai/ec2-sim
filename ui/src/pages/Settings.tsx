import { useEffect, useState } from "react";
import { api } from "../api";
import { Save } from "lucide-react";
import type { Settings } from "../api";
import { useStore } from "../store";
import { Button, Field, useToast } from "../ui";

const REGIONS = ["us-east-1", "us-east-2", "us-west-1", "us-west-2", "eu-west-1", "eu-central-1", "ap-southeast-1", "ap-southeast-3", "ap-northeast-1"];

export default function SettingsPage() {
  const { settings, setSettings, status, refresh } = useStore();
  const toast = useToast();
  const [form, setForm] = useState<Settings | null>(settings);
  useEffect(() => setForm(settings), [settings]);
  // Which ec2-sim the folder resolves to: a checkout, or the managed install.
  const [source, setSource] = useState("");
  const dir = form?.ec2sim_dir ?? "";
  useEffect(() => {
    const t = setTimeout(() => api.serverSource(dir).then(setSource, () => setSource("")), 300);
    return () => clearTimeout(t);
  }, [dir]);
  if (!form) return null;

  const set = <K extends keyof Settings>(k: K, v: Settings[K]) => setForm({ ...form, [k]: v });
  const dirty = JSON.stringify(form) !== JSON.stringify(settings);
  const serverSide =
    settings &&
    (form.ec2sim_dir !== settings.ec2sim_dir || form.extra_env !== settings.extra_env || form.backend !== settings.backend || form.wsl_distro !== settings.wsl_distro);

  return (
    <div className="page narrow">
      <section className="card stack">
        <h2>Simulator</h2>
        {status?.platform === "windows" && (
          <Field
            label="Run ec2-sim in"
            hint={
              status.via_wsl
                ? "Currently using WSL: the server, Docker commands, shells and SSH run inside WSL."
                : "Currently running natively (Docker Desktop on Windows)."
            }
          >
            <select value={form.backend} onChange={(e) => set("backend", e.target.value)}>
              <option value="auto">Automatic (Docker Desktop if available, else WSL)</option>
              <option value="wsl">WSL</option>
              <option value="native">Windows (Docker Desktop)</option>
            </select>
          </Field>
        )}
        {status?.platform === "windows" && form.backend !== "native" && (
          <Field label="WSL distro" hint="Leave empty for your default distro (wsl -l -v lists them).">
            <input value={form.wsl_distro} onChange={(e) => set("wsl_distro", e.target.value)} placeholder="default" spellCheck={false} />
          </Field>
        )}
        <Field
          label="ec2-sim folder (optional)"
          hint={
            <>
              {status?.via_wsl ? "A checkout inside WSL, e.g. ~/tools/ec2-sim. " : "A git checkout of ec2-sim (the folder with pyproject.toml). "}
              Leave it empty, or point it anywhere else, and the app installs the matching ec2-sim version itself with uv.
              {source && (
                <>
                  <br />
                  Using: <code>{source}</code>
                </>
              )}
            </>
          }
        >
          <input value={form.ec2sim_dir} onChange={(e) => set("ec2sim_dir", e.target.value)} placeholder="empty: managed install" spellCheck={false} />
        </Field>
        <Field label="API endpoint">
          <input value={form.endpoint} onChange={(e) => set("endpoint", e.target.value)} spellCheck={false} />
        </Field>
        <Field label="Region" hint="AMIs you create are kept per region.">
          <select value={form.region} onChange={(e) => set("region", e.target.value)}>
            {REGIONS.includes(form.region) ? null : <option>{form.region}</option>}
            {REGIONS.map((r) => (
              <option key={r}>{r}</option>
            ))}
          </select>
        </Field>
        <label className="check">
          <input type="checkbox" checked={form.auto_start} onChange={(e) => set("auto_start", e.target.checked)} />
          Start the server automatically when this app opens
        </label>
      </section>

      <section className="card stack">
        <h2>Advanced</h2>
        <Field
          label="Extra environment for the server"
          hint={
            <>
              One <code>KEY=VALUE</code> per line, e.g. <code>EC2SIM_AMI_ALIASES=ami-0123456789abcdef0=ubuntu-18.04</code> or{" "}
              <code>EC2SIM_NETWORK_MODE=bastion</code>. See the README's Configuration table.
            </>
          }
        >
          <textarea className="code" rows={5} value={form.extra_env} onChange={(e) => set("extra_env", e.target.value)} spellCheck={false} />
        </Field>
        <div className="muted small">
          Private keys are saved in <code>{status?.keys_dir}</code>.
        </div>
      </section>

      <div className="sticky-actions">
        {serverSide && status?.running && <span className="muted small">Restart the server to apply folder/environment changes.</span>}
        <Button
          variant="primary"
          icon={<Save size={16} />}
          disabled={!dirty}
          onClick={async () => {
            try {
              await setSettings(form);
              toast("success", "Settings saved");
              await refresh();
            } catch (e) {
              toast("error", String(e));
            }
          }}
        >
          Save
        </Button>
      </div>
    </div>
  );
}
