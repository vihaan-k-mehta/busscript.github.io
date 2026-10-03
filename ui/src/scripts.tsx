import { useCallback, useEffect, useRef, useState } from "react";
import { Play, Square, CheckCheck, Save, FolderOpen, Trash2 } from "lucide-react";
import { api, type BusState } from "./api";
import type { Notify } from "./ui";

interface Template { id: string; title: string; about: string; text: string }
interface Status {
  running: boolean; finished: boolean; line: number; ok: boolean; stopped: boolean;
  error: string | null; error_line: number | null; elapsed: number;
  output: { t: number; t_last: number; text: string; level: "info" | "warn" | "error"; count: number }[];
}

const DRAFT = "busscript-script-draft";
const STARTER = "# Pick an example above, or type your own steps here (one per line).\n# Lines that start with # are notes for you and are ignored.\nprint Hello from Busscript\n";

const COMMANDS: [string, string, string][] = [
  ["send", "Send a message using a signal name and a value", "send VehicleSpeed Speed=50"],
  ["sendraw", "Send raw bytes (id first, then hex bytes)", "sendraw 0x123 01 02 03"],
  ["wait", "Pause for a while (ms, s or m)", "wait 2s"],
  ["wait until", "Pause until a signal reaches a value, with an optional time limit", "wait until EngineData.EngineSpeed > 3000 timeout 30s"],
  ["repeat", "Do the steps up to the matching end, several times", "repeat 5 ... end"],
  ["every", "Do the steps over and over for a while", "every 100ms for 5s ... end"],
  ["log start / log stop", "Record everything on the bus to a file", "log start run1.asc"],
  ["print", "Write a note in the results. {Message.Signal} shows a live value", "print Speed is {VehicleSpeed.Speed}"],
  ["start / stop", "Start or stop the measurement", "start"],
];

function loadDraft(): string {
  try { return localStorage.getItem(DRAFT) ?? STARTER; } catch { return STARTER; }
}

export function ScriptsPage({ state, notify }: { state: BusState | null; notify: Notify }) {
  const [text, setText] = useState(loadDraft);
  const [templates, setTemplates] = useState<Template[]>([]);
  const [saved, setSaved] = useState<string[]>([]);
  const [pick, setPick] = useState("");
  const [name, setName] = useState("");
  const [status, setStatus] = useState<Status | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [good, setGood] = useState<string | null>(null);
  const area = useRef<HTMLTextAreaElement>(null);
  const outRef = useRef<HTMLElement>(null);

  useEffect(() => { try { localStorage.setItem(DRAFT, text); } catch { /* private window */ } }, [text]);
  useEffect(() => { api<Template[]>("/api/scripts/templates").then(setTemplates).catch(() => {}); }, []);
  const loadSaved = useCallback(() => api<string[]>("/api/scripts/saved").then(setSaved).catch(() => {}), []);
  useEffect(() => { loadSaved(); }, [loadSaved]);

  useEffect(() => {
    const tick = () => api<Status>("/api/scripts/status").then(setStatus).catch(() => {});
    tick();
    const t = window.setInterval(tick, 400);
    return () => window.clearInterval(t);
  }, []);

  const selectLine = (n: number) => {
    const el = area.current;
    if (!el) return;
    const lines = text.split("\n");
    const start = lines.slice(0, n - 1).reduce((a, l) => a + l.length + 1, 0);
    el.focus();
    el.setSelectionRange(start, start + (lines[n - 1]?.length ?? 0));
  };

  const showProblem = (msg: string) => {
    setGood(null);
    setProblem(msg);
    const m = /^Line (\d+):/.exec(msg);
    if (m) selectLine(Number(m[1]));
  };

  const check = async () => {
    setProblem(null); setGood(null);
    try {
      const r = await api<{ ok: boolean; message?: string; steps?: number }>("/api/scripts/check", "POST", { text });
      if (r.ok) setGood(`No mistakes found (${r.steps} step${r.steps === 1 ? "" : "s"}).`);
      else showProblem(r.message ?? "Something is wrong with this script.");
    } catch (e) { showProblem((e as Error).message); }
  };

  const run = async () => {
    setProblem(null); setGood(null);
    try {
      setStatus(await api<Status>("/api/scripts/run", "POST", { text }));
      outRef.current?.scrollIntoView({ block: "nearest", behavior: "smooth" });   // on a narrow window the results sit below the editor
    }
    catch (e) { showProblem((e as Error).message); }
  };

  const stop = async () => { try { setStatus(await api<Status>("/api/scripts/stop", "POST")); } catch (e) { notify((e as Error).message, "error"); } };

  const save = async () => {
    const n = name.trim();
    if (!n) return notify("Give the script a name first.", "error");
    if (!/^[A-Za-z0-9 _.-]{1,60}$/.test(n)) return notify("Script names can use letters, digits, spaces, dots, dashes and underscores (up to 60).", "error");
    try { await api(`/api/scripts/saved/${encodeURIComponent(n)}`, "PUT", { text }); notify(`Saved "${n}"`); loadSaved(); setPick(n); }
    catch (e) { notify((e as Error).message, "error"); }
  };
  const open = async () => {
    if (!pick) return;
    try { const r = await api<{ text: string }>(`/api/scripts/saved/${encodeURIComponent(pick)}`); setText(r.text); setName(pick); setProblem(null); setGood(null); }
    catch (e) { notify((e as Error).message, "error"); }
  };
  const del = async () => {
    if (!pick) return;
    try { await api(`/api/scripts/saved/${encodeURIComponent(pick)}`, "DELETE"); notify(`Deleted "${pick}"`); setPick(""); loadSaved(); }
    catch (e) { notify((e as Error).message, "error"); }
  };

  const running = !!status?.running;
  const lineText = status && status.line > 0 ? text.split("\n")[status.line - 1]?.trim() : "";
  const headline = running ? `Running. Now on line ${status!.line}: ${lineText}`
    : status?.finished ? (status.stopped ? "Stopped." : status.error ? "Stopped because of a problem (see below)." : "Finished.")
    : "Nothing has run yet.";

  return (
    <div className="scripts">
      <section className="card sc-intro" aria-labelledby="h-sc-intro">
        <h3 id="h-sc-intro">Scripts</h3>
        <div className="cb">
          <p style={{ margin: 0 }}>
            A script is a short list of steps that Busscript does for you, one step per line. You do not need to know any programming.
            <strong> Pick an example, press Run, and watch what happens in the results box.</strong>
          </p>
          {!state?.running && (
            <p className="note" role="note">The measurement is not running. Press <strong>Start</strong> at the top left first, or begin your script with a line that says <code>start</code>.</p>
          )}
          <p className="muted" style={{ margin: 0 }}>The examples use the demo traffic, so they work right away. With your own database, change the message and signal names to ones from your file.</p>
        </div>
      </section>

      <section className="card sc-ex" aria-labelledby="h-sc-ex">
        <h3 id="h-sc-ex">1. Start with an example</h3>
        <div className="cb">
          <div className="ex-grid">
            {templates.map((t) => (
              <button key={t.id} className="ex-card" onClick={() => { setText(t.text); setProblem(null); setGood(null); }}>
                <strong>{t.title}</strong><span>{t.about}</span>
              </button>
            ))}
          </div>
        </div>
      </section>

      <section className="card sc-ed" aria-labelledby="h-sc-ed">
        <h3 id="h-sc-ed">2. Look at it, change it, run it</h3>
        <div className="cb">
          <textarea ref={area} className="script-ed mono" aria-label="Script" rows={10} spellCheck={false} value={text}
            onChange={(e) => { setText(e.target.value); setProblem(null); setGood(null); }} aria-invalid={!!problem} />
          {problem && <div className="err-msg" role="alert"><strong>{problem}</strong></div>}
          {good && <div className="ok-msg" role="status">{good}</div>}
          <div className="row">
            <button className="btn primary fixed" onClick={run} disabled={running}><Play size={13} /> Run</button>
            <button className="btn fixed" onClick={stop} disabled={!running}><Square size={13} /> Stop</button>
            <button className="btn fixed" onClick={check}><CheckCheck size={13} /> Check for mistakes</button>
          </div>
          <div className="row">
            <label className="f"><span>Save this script as</span><input type="text" value={name} onChange={(e) => setName(e.target.value)} placeholder="My first script" /></label>
            <button className="btn fixed" onClick={save}><Save size={13} /> Save</button>
            <label className="f"><span>Saved scripts</span>
              <select value={pick} onChange={(e) => setPick(e.target.value)}>
                <option value="">{saved.length ? "Choose one…" : "None saved yet"}</option>
                {saved.map((n) => <option key={n}>{n}</option>)}
              </select>
            </label>
            <button className="btn fixed" onClick={open} disabled={!pick}><FolderOpen size={13} /> Open</button>
            <button className="btn danger fixed" onClick={del} disabled={!pick} aria-label="Delete the chosen saved script"><Trash2 size={13} /></button>
          </div>
        </div>
      </section>

      <section className="card sc-out" ref={outRef} aria-labelledby="h-sc-out">
        <h3 id="h-sc-out">3. What happened</h3>
        <div className="cb">
          <div role="status" aria-live="polite"><strong>{headline}</strong>{running ? ` (${status!.elapsed} s)` : ""}</div>
          {status && status.output.length > 0 ? (
            <table className="tbl">
              <thead><tr><th scope="col">Seconds</th><th scope="col">What happened</th></tr></thead>
              <tbody>
                {status.output.map((o, i) => (
                  <tr key={i}>
                    <td className="mono">{o.t.toFixed(1)}</td>
                    <td style={{ whiteSpace: "normal", color: o.level === "error" ? "var(--color-danger)" : o.level === "warn" ? "var(--color-warning)" : undefined }}>{o.text}{o.count > 1 && <strong>{` × ${o.count} (until ${o.t_last.toFixed(1)} s)`}</strong>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <div className="empty" style={{ padding: 0 }}>Results appear here when you press Run.</div>}
        </div>
      </section>

      <section className="card sc-ref" aria-labelledby="h-sc-ref">
        <h3 id="h-sc-ref">All the commands</h3>
        <div className="cb">
          <details>
            <summary>Show the list</summary>
            <table className="tbl">
              <thead><tr><th scope="col">Command</th><th scope="col">What it does</th><th scope="col">Example</th></tr></thead>
              <tbody>{COMMANDS.map(([c, d, ex]) => <tr key={c}><td><code>{c}</code></td><td style={{ whiteSpace: "normal" }}>{d}</td><td><code>{ex}</code></td></tr>)}</tbody>
            </table>
            <p className="muted">Times: <code>500ms</code>, <code>2s</code>, <code>1m</code>. Comparisons: <code>&gt; &lt; &gt;= &lt;= == !=</code>. Add <code>on can1</code> to the end of a send to pick a channel. Sending only works on a channel that is not listen-only (see Setup). Scripts stop on their own after 30 minutes.</p>
          </details>
        </div>
      </section>
    </div>
  );
}
