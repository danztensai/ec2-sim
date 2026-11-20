import { useEffect, useRef, useState } from "react";
import { listen } from "@tauri-apps/api/event";
import { ArrowDownToLine, Copy, Search } from "lucide-react";
import { api } from "../api";
import { useStore } from "../store";
import { Button, Empty } from "../ui";

function levelOf(line: string): string {
  if (/\bERROR\b|Traceback|Exception/.test(line)) return "log-error";
  if (/\bWARN(ING)?\b/.test(line)) return "log-warn";
  if (line.startsWith("[ui]")) return "log-ui";
  return "";
}

export default function Logs() {
  const { status } = useStore();
  const [lines, setLines] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [follow, setFollow] = useState(true);
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    api.serverLogs().then(setLines);
    const un = listen<string>("server-log", (e) => setLines((l) => (l.length > 3000 ? [...l.slice(-2500), e.payload] : [...l, e.payload])));
    return () => {
      un.then((f) => f());
    };
  }, []);

  useEffect(() => {
    if (follow && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [lines, follow]);

  const shown = query ? lines.filter((l) => l.toLowerCase().includes(query.toLowerCase())) : lines;

  return (
    <div className="page page-fill">
      <div className="toolbar">
        <div className="input-icon search">
          <Search size={16} />
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Filter log…" />
        </div>
        <label className="check">
          <input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} />
          <ArrowDownToLine size={14} /> Follow
        </label>
        <div className="spacer" />
        <Button size="sm" icon={<Copy size={15} />} onClick={() => navigator.clipboard.writeText(shown.join("\n"))}>
          Copy
        </Button>
      </div>
      {lines.length === 0 ? (
        <Empty icon={<Search size={28} />} title="No log output">
          {status?.running && !status.managed
            ? "The server was started outside this app, so its log is in that terminal."
            : "Start the server from this app to see its log here."}
        </Empty>
      ) : (
        <div className="logbox" ref={box}>
          {shown.map((l, n) => (
            <div key={n} className={`log-line ${levelOf(l)}`}>
              {l}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
