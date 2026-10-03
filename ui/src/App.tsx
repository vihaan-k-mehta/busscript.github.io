import { useCallback, useEffect, useRef, useState } from "react";
import { Play, Square, Bot, RotateCcw } from "lucide-react";
import { api, token, type BusState, type Channel } from "./api";
import { live, useLive } from "./live";
import { DataPane, GraphicPane, StatPane, TracePane, WritePane, type LogLine } from "./panes";
import { SetupPage } from "./setup";
import { McpDialog } from "./mcp";
import { DesignPage } from "./design";
import { Toast, type Notify } from "./ui";
import { Cell, Splitter, useLayout } from "./layout";

export function App() {
  const lv = useLive();
  const [route, setRoute] = useState(location.hash);
  const [tab, setTab] = useState<"Setup" | "Measurement">("Measurement");
  const [hex, setHex] = useState(true);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [state, setState] = useState<BusState | null>(null);
  const [plotted, setPlotted] = useState<string[]>([]);
  const [lines, setLines] = useState<LogLine[]>([]);
  const [toast, setToast] = useState<{ msg: string; kind: "info" | "error" } | null>(null);
  const [mcpOpen, setMcpOpen] = useState(false);
  const [mcpOn, setMcpOn] = useState(false);
  const seenMcp = useRef(0);
  const { sizes, set, reset, isDefault } = useLayout();
  const rootRef = useRef<HTMLDivElement>(null);
  const topRef = useRef<HTMLDivElement>(null);
  const botRef = useRef<HTMLDivElement>(null);
  const leftRef = useRef<HTMLDivElement>(null);

  useEffect(() => { live.start(); }, []);
  useEffect(() => {
    const on = () => setRoute(location.hash);
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);

  const write = useCallback((source: string, text: string, level: LogLine["level"] = "info") =>
    setLines((l) => [...l.slice(-499), { ts: Date.now(), source, level, text }]), []);

  const notify: Notify = useCallback((msg, kind = "info") => {
    setToast({ msg, kind });
    write("System", msg, kind === "error" ? "error" : "info");
  }, [write]);

  const refresh = useCallback(async () => {
    try {
      const [c, s] = await Promise.all([api<Channel[]>("/api/channels"), api<BusState>("/api/state")]);
      setChannels(c); setState(s);
    } catch (e) { notify((e as Error).message, "error"); }
  }, [notify]);

  useEffect(() => { refresh(); }, [refresh]);
  // the websocket tick carries fresh state once a second
  useEffect(() => { if (lv.state) setState(lv.state); }, [lv.version, lv.state]);

  // pull MCP activity into the Write pane
  useEffect(() => {
    const t = window.setInterval(async () => {
      try {
        const [a, s] = await Promise.all([
          api<{ ts: number; tool: string; ok: boolean; detail: string }[]>("/api/mcp/activity"),
          api<Record<string, string>>("/api/settings"),
        ]);
        setMcpOn(s["mcp.enabled"] === "true");
        const fresh = a.filter((x) => x.ts > seenMcp.current).reverse();
        if (fresh.length) {
          seenMcp.current = Math.max(...fresh.map((x) => x.ts));
          fresh.forEach((x) => write("MCP", `${x.tool} ${x.ok ? "ok" : "refused: " + x.detail}`, x.ok ? "info" : "warn"));
        }
      } catch { /* offline */ }
    }, 2000);
    return () => window.clearInterval(t);
  }, [write]);

  const running = !!state?.running;
  const startStop = async () => {
    try {
      if (running) { await api("/api/measurement/stop", "POST"); write("System", "End of measurement"); }
      else {
        live.clear();
        await api("/api/measurement/start", "POST"); write("System", "Start of measurement");
      }
      await refresh();
    } catch (e) { notify((e as Error).message, "error"); }
  };

  const onPlot = useCallback((name: string, on: boolean) =>
    setPlotted((p) => (on ? (p.includes(name) ? p : [...p, name]) : p.filter((n) => n !== name))), []);

  if (route === "#/design") return <><DesignPage /></>;

  const load = state?.running ? lv.stats : {};
  const busLoad = Math.max(0, ...Object.values(load).map((s) => s.load_pct));

  return (
    <div className="shell">
      <div className="menubar" role="banner">
        <span className="title">Busscript</span>
        <a href="#/design">Design</a>
        <button className="link" onClick={() => setMcpOpen(true)}><Bot size={13} style={{ verticalAlign: "-2px" }} /> MCP</button>
      </div>
      <div className="toolbar" role="toolbar" aria-label="Measurement">
        <button className={`tb ${running ? "stopbtn" : "run"}`} onClick={startStop}>
          {running ? <Square size={14} /> : <Play size={14} />}<span>{running ? "Stop" : "Start"}</span>
        </button>
        <span className="sep" />
        <button className="tb" aria-pressed={hex} onClick={() => setHex(!hex)} title="Show IDs and data in hexadecimal or decimal">{hex ? "hex" : "dec"}</button>
        <button className="tb" onClick={reset} disabled={isDefault || tab !== "Measurement"} title="Put the panes back to their default sizes"><RotateCcw size={13} /><span>Reset layout</span></button>
        <span className="muted" style={{ marginLeft: "auto" }}>
          {state?.replay ? "Replaying · " : ""}{state?.logging ? "Logging · " : ""}{running ? `${state?.elapsed.toFixed(0)} s` : "Stopped"}
        </span>
      </div>
      {!lv.connected && <div className="banner" role="alert">Disconnected from the analyzer core. Reconnecting…{!token && " No token: open the link the server printed."}</div>}
      <main className="content">
        {tab === "Setup" ? (
          <SetupPage channels={channels} state={state} refresh={refresh} notify={notify} />
        ) : (
          <div className="layout" ref={rootRef}>
            <div className="lrow" ref={topRef} style={{ flex: `${sizes.topH} 1 0` }}>
              <Cell grow={sizes.topSplit}><TracePane hex={hex} /></Cell>
              <Splitter orientation="vertical" container={topRef} value={sizes.topSplit} onChange={(v) => set("topSplit", v)} onReset={reset} label="Resize Trace and Bus statistic" />
              <Cell grow={1 - sizes.topSplit}><StatPane /></Cell>
            </div>
            <Splitter orientation="horizontal" container={rootRef} value={sizes.topH} onChange={(v) => set("topH", v)} onReset={reset} label="Resize top and bottom panes" />
            <div className="lrow" ref={botRef} style={{ flex: `${1 - sizes.topH} 1 0` }}>
              <div className="lcol" ref={leftRef} style={{ flex: `${sizes.botSplit} 1 0` }}>
                <Cell grow={sizes.leftSplit}><DataPane running={running} plotted={plotted} onPlot={onPlot} /></Cell>
                <Splitter orientation="horizontal" container={leftRef} value={sizes.leftSplit} onChange={(v) => set("leftSplit", v)} onReset={reset} label="Resize Data and Write" />
                <Cell grow={1 - sizes.leftSplit}><WritePane lines={lines} /></Cell>
              </div>
              <Splitter orientation="vertical" container={botRef} value={sizes.botSplit} onChange={(v) => set("botSplit", v)} onReset={reset} label="Resize Data and Write against Graphic" />
              <Cell grow={1 - sizes.botSplit}><GraphicPane plotted={plotted} onPlot={onPlot} /></Cell>
            </div>
          </div>
        )}
      </main>
      <div className="tabs" role="tablist" aria-label="View">
        {(["Setup", "Measurement"] as const).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>
      <div className="statusbar" role="status">
        <span><span className={`dot ${running ? "ok" : ""}`} />{running ? "Measuring" : "Stopped"}</span>
        <span>Bus load {busLoad.toFixed(1)} %</span>
        <span>{channels.length} channel{channels.length === 1 ? "" : "s"}</span>
        <span><span className={`dot ${mcpOn ? "ok" : ""}`} />MCP {mcpOn ? "on" : "off"}</span>
      </div>
      {toast && <Toast msg={toast.msg} kind={toast.kind} onDone={() => setToast(null)} />}
      <McpDialog open={mcpOpen} onClose={() => setMcpOpen(false)} notify={notify} />
    </div>
  );
}
