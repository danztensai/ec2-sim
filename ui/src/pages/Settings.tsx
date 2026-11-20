import { useEffect, useState } from "react";
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
  if (!form) return null;

  const set = <K extends keyof Settings>(k: K, v: Settings[K]) => setForm({ ...form, [k]: v });
  const dirty = JSON.stringify(form) !== JSON.stringify(settings);
  const serverSide = settings && (form.ec2sim_dir !== settings.ec2sim_dir || form.extra_env !== settings.extra_env);

  return (
    <div className="page narrow">
      <section className="card stack">
        <h2>Simulator</h2>
        <Field label="ec2-sim folder" hint="The ec2-sim checkout this app starts (the folder with pyproject.toml).">
          <input value={form.ec2sim_dir} onChange={(e) => set("ec2sim_dir", e.target.value)} spellCheck={false} />
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
