// Run a project's own command (e.g. a Packer AMI build script), unmodified,
// against ec2-sim — via `ec2-sim run` in a disposable runner container.
import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { Copy, Eye, EyeOff, Lock, Play, Plus, Square, TerminalSquare, Trash2 } from "lucide-react";
import { useStore } from "../store";
import { Button, Empty, Field, Tag, useToast } from "../ui";

type Var = { key: string; value: string; secret: boolean };
type Saved = { dir: string; command: string; region: string; vars: Var[] };

const STORE_KEY = "ec2sim.runScript";
const REGIONS = ["us-east-1", "us-east-2", "us-west-1", "us-west-2", "eu-west-1", "eu-central-1", "ap-southeast-1", "ap-southeast-3", "ap-northeast-1"];

function load(): Saved | null {
  try {
    return JSON.parse(localStorage.getItem(STORE_KEY) ?? "null");
  } catch {
    return null;
  }
}

function save(s: Saved) {
  // Secret values are never persisted (only their names).
  const vars = s.vars.map((v) => (v.secret ? { ...v, value: "" } : v));
  try {
    localStorage.setItem(STORE_KEY, JSON.stringify({ ...s, vars }));
  } catch {
    /* storage unavailable */
  }
}

function levelOf(line: string): string {
  if (/\berror\b|errored|Error:|Traceback|FAIL/i.test(line)) return "log-error";
  if (/\bwarn(ing)?\b/i.test(line)) return "log-warn";
  if (line.startsWith("[ec2-sim run]")) return "log-ui";
  return "";
}

export default function RunScript() {
  const { status, settings } = useStore();
  const toast = useToast();
  const saved = useRef(load());
  const [dir, setDir] = useState(saved.current?.dir ?? "");
  const [command, setCommand] = useState(saved.current?.command ?? "");
  const [region, setRegion] = useState(saved.current?.region ?? settings?.region ?? "us-east-1");
  const [vars, setVars] = useState<Var[]>(saved.current?.vars ?? []);
  const [show, setShow] = useState<Record<number, boolean>>({});
  const [running, setRunning] = useState(false);
  const [exitCode, setExitCode] = useState<number | null>(null);
  const [lines, setLines] = useState<string[]>([]);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    invoke<boolean>("script_running").then(setRunning);
    const a = listen<string>("run-output", (e) => setLines((l) => (l.length > 20000 ? [...l.slice(-15000), e.payload] : [...l, e.payload])));
    const b = listen<number>("run-exit", (e) => {
      setRunning(false);
      setExitCode(e.payload);
    });
    return () => {
      a.then((f) => f());
      b.then((f) => f());
    };
  }, []);

  useEffect(() => {
    if (box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [lines]);

  const setVar = (i: number, patch: Partial<Var>) => setVars((vs) => vs.map((v, n) => (n === i ? { ...v, ...patch } : v)));
  const missing = vars.filter((v) => v.key && !v.value).map((v) => v.key);

  async function run() {
    const env = vars.filter((v) => v.key.trim()).map((v) => [v.key.trim(), v.value] as [string, string]);
    save({ dir, command, region, vars });
    setLines([]);
    setExitCode(null);
    try {
      await invoke("run_script", { dir, command, region, env });
      setRunning(true);
    } catch (e) {
      toast("error", String(e));
    }
  }

  async function stop() {
    await invoke("stop_script").catch((e) => toast("error", String(e)));
    setRunning(false);
  }

  if (!status?.running) {
    return <Empty icon={<TerminalSquare size={28} />} title="Server is stopped" children="Start the server, then run your scripts against it." />;
  }

  return (
    <div className="page page-fill">
      <section className="card stack">
        <p className="muted">
          Runs <b>your command, unmodified</b>, in a disposable runner container (Packer + Amazon plugin, AWS CLI v2, jq, sudo…).
          AWS calls go to ec2-sim through its proxy, so scripts need no endpoint changes. Only fake AWS credentials exist inside,
          and your folder is mounted at the same path.
        </p>
        <div className="grid-run">
          <Field label="Folder" hint={status.via_wsl ? "Path inside WSL, e.g. /mnt/d/work/project/main" : "Absolute path of the folder to run in"}>
            <input value={dir} onChange={(e) => setDir(e.target.value)} placeholder="/path/to/repo" spellCheck={false} disabled={running} />
          </Field>
          <Field label="Region" hint="AWS_DEFAULT_REGION inside the run">
            <select value={region} onChange={(e) => setRegion(e.target.value)} disabled={running}>
              {REGIONS.includes(region) ? null : <option>{region}</option>}
              {REGIONS.map((r) => (
                <option key={r}>{r}</option>
              ))}
            </select>
          </Field>
        </div>
        <Field label="Command" hint="Run exactly as you would on the build host, e.g. ./buildAmi.sh -e staging -a">
          <input className="mono-input" value={command} onChange={(e) => setCommand(e.target.value)} placeholder="./build.sh" spellCheck={false} disabled={running} />
        </Field>

        <div className="stack-sm">
          <div className="field-label">Environment variables</div>
          {vars.length === 0 && <div className="muted small">None. Add variables your script expects (e.g. tokens), and mark secrets so they're masked and never saved.</div>}
          {vars.map((v, i) => (
            <div key={i} className="var-row">
              <input className="mono-input" value={v.key} onChange={(e) => setVar(i, { key: e.target.value.toUpperCase().replace(/[^A-Z0-9_]/g, "_") })} placeholder="NAME" disabled={running} />
              <div className="input-icon var-value">
                {v.secret && <Lock size={14} />}
                <input
                  type={v.secret && !show[i] ? "password" : "text"}
                  className="mono-input"
                  value={v.value}
                  onChange={(e) => setVar(i, { value: e.target.value })}
                  placeholder={v.secret ? "secret value (not saved)" : "value"}
                  disabled={running}
                  autoComplete="off"
                />
              </div>
              {v.secret && (
                <button className="icon-btn" title={show[i] ? "Hide" : "Show"} onClick={() => setShow((s) => ({ ...s, [i]: !s[i] }))}>
                  {show[i] ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              )}
              <label className="check small" title="Masked and never saved to disk">
                <input type="checkbox" checked={v.secret} onChange={(e) => setVar(i, { secret: e.target.checked })} disabled={running} />
                Secret
              </label>
              <button className="icon-btn" title="Remove" aria-label="Remove variable" onClick={() => setVars((vs) => vs.filter((_, n) => n !== i))} disabled={running}>
                <Trash2 size={16} />
              </button>
            </div>
          ))}
          <div>
            <Button size="sm" variant="ghost" icon={<Plus size={15} />} onClick={() => setVars((vs) => [...vs, { key: "", value: "", secret: false }])} disabled={running}>
              Add variable
            </Button>
          </div>
        </div>

        <div className="run-actions">
          {running ? (
            <Button variant="danger" icon={<Square size={15} />} onClick={stop}>
              Stop
            </Button>
          ) : (
            <Button variant="primary" icon={<Play size={15} />} onClick={run} disabled={!dir.trim() || !command.trim() || missing.length > 0}>
              Run
            </Button>
          )}
          {missing.length > 0 && !running && <span className="muted small">Fill in: {missing.join(", ")}</span>}
          {running && <Tag tone="warn">Running…</Tag>}
          {!running && exitCode !== null && <Tag tone={exitCode === 0 ? "success" : "warn"}>{exitCode === 0 ? "Succeeded (exit 0)" : `Failed (exit ${exitCode})`}</Tag>}
          <div className="spacer" />
          {lines.length > 0 && (
            <Button size="sm" icon={<Copy size={15} />} onClick={() => navigator.clipboard.writeText(lines.join("\n"))}>
              Copy output
            </Button>
          )}
        </div>
      </section>

      {lines.length > 0 && (
        <div className="logbox" ref={box}>
          {lines.map((l, n) => (
            <div key={n} className={`log-line ${levelOf(l)}`}>
              {l}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
