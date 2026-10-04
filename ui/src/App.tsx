import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Play, Square, Bot, RotateCcw, FolderOpen } from "lucide-react";
import { api, token, type BusState, type Channel, type FileSummary, type OpenResult } from "./api";
import { live, useLive } from "./live";
import { DataPane, GraphicPane, StatPane, TracePane, WritePane, type LogLine } from "./panes";
import { SetupPage } from "./setup";
import { McpDialog } from "./mcp";
import { ScriptsPage } from "./scripts";
import { DiagnosticsPage } from "./diag";
import { AnalyzePage } from "./analyze";
import { DesignPage } from "./design";
import { Toast, type Notify } from "./ui";
import { Cell, PANE_IDS, PANE_NAMES, PaneContext, Splitter, ViewMenu, useLayout, type PaneControls, type PaneId } from "./layout";
import { DragGhost, lineChannel, useHeaderDrag, usePopped, useShared } from "./dock";
import { BusyOverlay, DropOverlay, OpenFileDialog, describe, importFiles, type Busy } from "./openfile";
import { FileInfoPane, FileTracePane } from "./filepane";

export function App() {
  const lv = useLive();
  const [route, setRoute] = useState(location.hash);
  const [tab, setTab] = useState<"Setup" | "Measurement" | "Scripts" | "Diagnostics" | "Analyze">("Measurement");
  const [hex, setHex] = useShared<boolean>("busscript-hex", true);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [state, setState] = useState<BusState | null>(null);
  const [plotted, setPlotted] = useShared<string[]>("busscript-plotted", []);
  const [lines, setLines] = useState<LogLine[]>([]);
  const [toast, setToast] = useState<{ msg: string; kind: "info" | "error" } | null>(null);
  const [mcpOpen, setMcpOpen] = useState(false);
  const [mcpOn, setMcpOn] = useState(false);
  const seenMcp = useRef(0);
  const [fileInfo, setFileInfo] = useState<FileSummary | null>(null);
  const [fileRev, setFileRev] = useState(0);                  // bumped when a database is attached so names refresh
  const [busy, setBusy] = useState<Busy | null>(null);
  const [openDlg, setOpenDlg] = useState(false);
  const [dragging, setDragging] = useState(false);
  const { sizes, set, resetSizes, reset, isDefault, order, hidden, controls: baseControls, setVisible } = useLayout();
  const { popped, popOut, dockBack } = usePopped();
  const drag = useHeaderDrag(baseControls.swap, popOut);
  const controls: PaneControls = { ...baseControls, drag, popOut, dockBack };
  const resetAll = () => { reset(); popped.forEach(dockBack); };         // panes in their own windows come back too
  const nothingMoved = isDefault && popped.length === 0;
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

  const write = useCallback((source: string, text: string, level: LogLine["level"] = "info") => {
    const line = { ts: Date.now(), source, level, text };
    setLines((l) => [...l.slice(-499), line]);
    lineChannel?.postMessage(line);                         // a Write pane in its own window shows it too
  }, []);

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
        setFileInfo(null);                                  // going live closes any open recording
      }
      await refresh();
    } catch (e) { notify((e as Error).message, "error"); }
  };

  const refreshFile = useCallback(() => api<FileSummary | null>("/api/file").then(setFileInfo).catch(() => {}), []);
  useEffect(() => { refreshFile(); }, [refreshFile]);

  const opened = useCallback((r: OpenResult) => {
    if (r.kind === "log") {
      const { kind: _k, ...info } = r;
      setFileInfo(info);
      setTab("Measurement");
    } else {
      setFileRev((n) => n + 1);
      refresh();
    }
    notify(describe(r));
  }, [notify, refresh]);

  const pickFiles = useCallback((files: File[]) => { importFiles(files, { busy: setBusy, notify, opened }); }, [notify, opened]);

  const openStored = useCallback(async (name: string) => {
    setBusy({ text: `Reading ${name}…` });
    try { opened(await api<OpenResult>("/api/files/open", "POST", { name })); }
    catch (e) { notify((e as Error).message, "error"); }
    finally { setBusy(null); }
  }, [notify, opened]);

  const leaveDemo = async () => {
    try {
      await api("/api/demo/off", "POST");
      await refresh();
      setTab("Setup");
      notify("Demo stopped. Add your adapter as a channel below.");
    } catch (e) { notify((e as Error).message, "error"); }
  };
  const closeFile = async () => { try { await api("/api/file", "DELETE"); setFileInfo(null); } catch (e) { notify((e as Error).message, "error"); } };

  // dragging a file anywhere onto the window opens it
  useEffect(() => {
    const hasFiles = (e: DragEvent) => !!e.dataTransfer && [...e.dataTransfer.types].includes("Files");
    const over = (e: DragEvent) => { if (hasFiles(e)) { e.preventDefault(); setDragging(true); } };
    const leave = (e: DragEvent) => { if (!e.relatedTarget) setDragging(false); };
    const drop = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault(); setDragging(false);
      const fs = [...(e.dataTransfer?.files ?? [])];
      if (fs.length) pickFiles(fs);
    };
    window.addEventListener("dragover", over); window.addEventListener("dragleave", leave); window.addEventListener("drop", drop);
    return () => { window.removeEventListener("dragover", over); window.removeEventListener("dragleave", leave); window.removeEventListener("drop", drop); };
  }, [pickFiles]);

  const onPlot = useCallback((name: string, on: boolean) =>
    setPlotted((p) => (on ? (p.includes(name) ? p : [...p, name]) : p.filter((n) => n !== name))), [setPlotted]);

  if (route === "#/design") return <><DesignPage /></>;

  const load = state?.running ? lv.stats : {};
  const fileKey = fileInfo ? `${fileInfo.name}|${fileInfo.frames}|${fileRev}` : undefined;
  const node: Record<PaneId, ReactNode> = {
    trace: fileInfo ? <FileTracePane info={fileInfo} hex={hex} rev={fileRev} /> : <TracePane hex={hex} />,
    stat: fileInfo ? <FileInfoPane info={fileInfo} rev={fileRev} /> : <StatPane />,
    data: <DataPane running={running} plotted={plotted} onPlot={onPlot} fileKey={fileKey} />,
    write: <WritePane lines={lines} />,
    graphic: <GraphicPane plotted={plotted} onPlot={onPlot} fileKey={fileKey} />,
  };
  // the five places on the page: two on top, two stacked at the bottom left, one at the bottom right
  const shown = (id: PaneId) => !hidden.includes(id) && !popped.includes(id);
  const topIds = [order[0], order[1]].filter(shown);
  const leftIds = [order[2], order[3]].filter(shown);
  const rightId = shown(order[4]) ? order[4] : null;
  const bottomShown = leftIds.length > 0 || rightId !== null;
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
        <button className="tb" onClick={() => setOpenDlg(true)} title="Open a recording or a database file"><FolderOpen size={14} /><span>Open file</span></button>
        <span className="sep" />
        <button className="tb" aria-pressed={hex} onClick={() => setHex(!hex)} title="Show IDs and data in hexadecimal or decimal">{hex ? "hex" : "dec"}</button>
        {tab === "Measurement" && <ViewMenu hidden={hidden} popped={popped} onToggle={setVisible} onDock={dockBack} onReset={resetAll} canReset={!nothingMoved} />}
        <button className="tb" onClick={resetAll} disabled={nothingMoved || tab !== "Measurement"} title="Put the panes back where they started, at their default sizes"><RotateCcw size={13} /><span>Reset layout</span></button>
        <span className="muted" style={{ marginLeft: "auto" }}>
          {state?.replay ? "Replaying · " : ""}{state?.logging ? "Logging · " : ""}{running ? `${state?.elapsed.toFixed(0)} s` : "Stopped"}
        </span>
      </div>
      {state?.demo && !fileInfo && <div className="banner demo" role="status"><span><strong>Demo data.</strong> This is synthetic traffic from a built-in test bus, not a real CAN bus. Ready for a real adapter?</span> <button className="btn" onClick={leaveDemo}>Use my own adapter</button></div>}
      {fileInfo && (
        <div className="banner file" role="status">
          <span><strong>Viewing a file:</strong> {fileInfo.name} ({fileInfo.format}, {fileInfo.frames.toLocaleString()} frames, {fileInfo.duration} s). This is a recording, not live data.</span>
          <button className="btn" onClick={closeFile}>Close file</button>
        </div>
      )}
      {!lv.connected && <div className="banner" role="alert">Disconnected from the analyzer core. Reconnecting…{!token && " No token: open the link the server printed."}</div>}
      <main className="content">
        {tab === "Setup" ? (
          <SetupPage channels={channels} state={state} refresh={refresh} notify={notify} onImport={pickFiles} />
        ) : tab === "Analyze" ? (
          <AnalyzePage file={fileInfo} notify={notify} />
        ) : tab === "Diagnostics" ? (
          <DiagnosticsPage rev={fileRev + (fileInfo?.frames ?? 0)} />
        ) : tab === "Scripts" ? (
          <ScriptsPage state={state} notify={notify} />
        ) : (
          <PaneContext.Provider value={controls}>
            <div className="layout" ref={rootRef}>
              {PANE_IDS.every((id) => !shown(id)) && (
                <div className="empty" style={{ margin: "auto" }}>All panes are hidden or in their own windows. Open <b>View</b> in the toolbar to bring them back.</div>
              )}
              {topIds.length > 0 && (
                <div className="lrow" ref={topRef} style={{ flex: `${bottomShown ? sizes.topH : 1} 1 0` }}>
                  {topIds.length === 2 ? (
                    <>
                      <Cell grow={sizes.topSplit}>{node[topIds[0]]}</Cell>
                      <Splitter orientation="vertical" container={topRef} value={sizes.topSplit} onChange={(v) => set("topSplit", v)} onReset={resetSizes} label={`Resize ${PANE_NAMES[topIds[0]]} and ${PANE_NAMES[topIds[1]]}`} />
                      <Cell grow={1 - sizes.topSplit}>{node[topIds[1]]}</Cell>
                    </>
                  ) : <Cell grow={1}>{node[topIds[0]]}</Cell>}
                </div>
              )}
              {topIds.length > 0 && bottomShown && (
                <Splitter orientation="horizontal" container={rootRef} value={sizes.topH} onChange={(v) => set("topH", v)} onReset={resetSizes} label="Resize top and bottom panes" />
              )}
              {bottomShown && (
                <div className="lrow" ref={botRef} style={{ flex: `${topIds.length > 0 ? 1 - sizes.topH : 1} 1 0` }}>
                  {leftIds.length > 0 && (
                    <div className="lcol" ref={leftRef} style={{ flex: `${rightId ? sizes.botSplit : 1} 1 0` }}>
                      {leftIds.length === 2 ? (
                        <>
                          <Cell grow={sizes.leftSplit}>{node[leftIds[0]]}</Cell>
                          <Splitter orientation="horizontal" container={leftRef} value={sizes.leftSplit} onChange={(v) => set("leftSplit", v)} onReset={resetSizes} label={`Resize ${PANE_NAMES[leftIds[0]]} and ${PANE_NAMES[leftIds[1]]}`} />
                          <Cell grow={1 - sizes.leftSplit}>{node[leftIds[1]]}</Cell>
                        </>
                      ) : <Cell grow={1}>{node[leftIds[0]]}</Cell>}
                    </div>
                  )}
                  {leftIds.length > 0 && rightId && (
                    <Splitter orientation="vertical" container={botRef} value={sizes.botSplit} onChange={(v) => set("botSplit", v)} onReset={resetSizes} label={`Resize ${leftIds.map((i) => PANE_NAMES[i]).join(" and ")} against ${PANE_NAMES[rightId]}`} />
                  )}
                  {rightId && <Cell grow={leftIds.length > 0 ? 1 - sizes.botSplit : 1}>{node[rightId]}</Cell>}
                </div>
              )}
            </div>
          </PaneContext.Provider>
        )}
      </main>
      <div className="tabs" role="tablist" aria-label="Pages">
        {(["Setup", "Measurement", "Scripts", "Diagnostics", "Analyze"] as const).map((t) => (
          <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>{t}</button>
        ))}
      </div>
      <div className="statusbar" role="status">
        <span><span className={`dot ${running ? "ok" : ""}`} />{running ? "Measuring" : "Stopped"}</span>
        <span>Bus load {busLoad.toFixed(1)} %</span>
        <span>{channels.length} channel{channels.length === 1 ? "" : "s"}</span>
        <span><span className={`dot ${mcpOn ? "ok" : ""}`} />MCP {mcpOn ? "on" : "off"}</span>
      </div>
      <DragGhost drag={drag.state} />
      {toast && <Toast msg={toast.msg} kind={toast.kind} onDone={() => setToast(null)} />}
      <BusyOverlay busy={busy} />
      <DropOverlay show={dragging} />
      <OpenFileDialog open={openDlg} onClose={() => setOpenDlg(false)} onPick={pickFiles} onOpenStored={openStored} notify={notify} />
      <McpDialog open={mcpOpen} onClose={() => setMcpOpen(false)} notify={notify} />
    </div>
  );
}
