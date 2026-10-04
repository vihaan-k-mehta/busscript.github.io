import { Meaning } from "./diag";
import { useEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import uPlot from "uplot";
import "uplot/dist/uPlot.min.css";
import { Eraser, Pause, Play, ListTree, X } from "lucide-react";
import { api, type CatalogueMsg, type Frame, type SignalValue } from "./api";
import { live, useLive } from "./live";
import { bytesDec, decId, hexId, spaced, timeS } from "./format";
import { Pane } from "./ui";

const ROW = 17;

/* ------------------------------------------------------------------ Trace */
export function TracePane({ hex }: { hex: boolean }) {
  const lv = useLive();
  const [paused, setPaused] = useState(false);
  const [frozenLen, setFrozenLen] = useState(0);
  const [follow, setFollow] = useState(true);
  const [sel, setSel] = useState<number | null>(null);
  const [expanded, setExpanded] = useState<Record<number, { message: string; signal: string; value: unknown; unit: string }[]>>({});
  const parent = useRef<HTMLDivElement>(null);

  const rows = lv.frames;
  const count = paused ? Math.min(frozenLen, rows.length) : rows.length;
  // an expanded row adds one extra row of decoded text; keep it simple: show decoded below the table
  const v = useVirtualizer({ count, getScrollElement: () => parent.current, estimateSize: () => ROW, overscan: 20 });

  useEffect(() => {
    if (follow && !paused && count > 0) v.scrollToIndex(count - 1, { align: "end" });
  }, [count, follow, paused, v]);

  const toggle = async (i: number, f: Frame) => {
    setSel(i);
    if (expanded[i]) { setExpanded(({ [i]: _, ...rest }) => rest); return; }
    try {
      const d = await api<{ message: string; signal: string; value: unknown; unit: string }[]>(
        `/api/frames/decode?channel=${encodeURIComponent(f.ch)}&id=${f.id}&ext=${f.ext}&data=${f.data}`);
      setExpanded((e) => ({ ...e, [i]: d }));
    } catch { /* ignore: decoding is optional */ }
  };
  const selected = sel !== null ? rows[sel] : undefined;

  const tools = (
    <>
      <button className="tb" aria-pressed={paused} onClick={() => { setFrozenLen(rows.length); setPaused(!paused); }}
        aria-label={paused ? "Resume trace" : "Pause trace"} title={paused ? "Resume" : "Pause"}>
        {paused ? <Play size={14} /> : <Pause size={14} />}<span>{paused ? "Resume" : "Pause"}</span>
      </button>
      <button className="tb" onClick={() => { live.clear(); setExpanded({}); setSel(null); setFrozenLen(0); }} aria-label="Clear trace"><Eraser size={14} /><span>Clear</span></button>
      <label className="tb"><input type="checkbox" checked={follow} onChange={(e) => setFollow(e.target.checked)} /> <span>Follow</span></label>
      <span className="muted" style={{ marginLeft: "auto" }}>{count.toLocaleString()} frames{lv.dropped ? ` · ${lv.dropped} dropped (UI too slow)` : ""}</span>
    </>
  );

  return (
    <Pane paneId="trace" title="Trace" icon={<ListTree size={14} />} tools={tools}>
      <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
      <div ref={parent} style={{ flex: 1, minHeight: 0, overflow: "auto" }} role={count > 0 ? "grid" : undefined} aria-rowcount={count > 0 ? count : undefined} aria-label={count > 0 ? "Frames" : undefined}>
        {count === 0 ? (
          <div className="empty">No frames yet. Start the measurement, or load a log and replay it.</div>
        ) : (
          <div className="vtable" style={{ height: v.getTotalSize() + 20 }}>
            <div className="vhead" role="row">
              {["Time [s]", "Chn", "Dir", "ID", "Name", "DLC", "Data"].map((h) => <span key={h} role="columnheader">{h}</span>)}
            </div>
            {v.getVirtualItems().map((it) => {
              const f = rows[it.index];
              if (!f) return null;
              return (
                <div key={it.index} className={`vrow ${f.dir === "tx" ? "tx" : ""} ${f.error ? "err" : ""} ${sel === it.index ? "sel" : ""}`}
                  style={{ top: 20 + it.start }} role="row" tabIndex={0} aria-rowindex={it.index + 1}
                  onClick={() => toggle(it.index, f)} onKeyDown={(e) => e.key === "Enter" && toggle(it.index, f)}>
                  <span role="gridcell" className="mono">{timeS(f.ts)}</span>
                  <span role="gridcell">{f.ch}</span>
                  <span role="gridcell">{f.error ? "ERR" : f.dir === "tx" ? "Tx" : "Rx"}</span>
                  <span role="gridcell" className="mono">{f.error ? "" : hex ? hexId(f) : decId(f)}</span>
                  <span role="gridcell">{f.name ?? (f.error ? "Error frame" : "—")}</span>
                  <span role="gridcell" className="mono">{f.dlc}</span>
                  <span role="gridcell" className="mono">{hex ? spaced(f.data) : bytesDec(f.data)}</span>
                </div>
              );
            })}
          </div>
        )}
      </div>
      {selected && expanded[sel!] && (
        <div className="decoded" style={{ position: "static", flex: "none" }} aria-live="polite">
          <Meaning id={selected.id} ext={selected.ext} data={selected.data} />
          {expanded[sel!].length === 0 ? <span className="muted">No database entry for this ID.</span> :
            expanded[sel!].map((s) => <span key={s.signal} style={{ marginRight: 16 }}>{s.signal} = <b>{String(s.value)}</b> {s.unit}</span>)}
        </div>
      )}
      </div>
    </Pane>
  );
}

/* ------------------------------------------------------------------- Data */
export function DataPane({ running, plotted, onPlot, fileKey }: { running: boolean; plotted: string[]; onPlot: (name: string, on: boolean) => void; fileKey?: string }) {
  const [vals, setVals] = useState<SignalValue[]>([]);
  const [cat, setCat] = useState<CatalogueMsg[]>([]);
  const [paused, setPaused] = useState(false);

  const fileMode = fileKey !== undefined;
  useEffect(() => { api<CatalogueMsg[]>(fileMode ? "/api/file/signals" : "/api/signals").then(setCat).catch(() => {}); }, [running, fileMode, fileKey]);
  useEffect(() => {
    if (paused && !fileMode) return;
    const tick = () => api<SignalValue[]>(fileMode ? "/api/file/signal-values" : "/api/signal-values").then(setVals).catch(() => setVals([]));
    tick();
    if (fileMode) return;                       // a file does not change, so read it once
    const t = window.setInterval(tick, 500);
    return () => window.clearInterval(t);
  }, [paused, fileMode, fileKey]);

  const range = useMemo(() => {
    const m = new Map<string, [number | null, number | null]>();
    for (const c of cat) for (const s of c.signals) m.set(`${c.name}.${s.name}`, [s.min, s.max]);
    return m;
  }, [cat]);

  const tools = (
    <>
      <button className="tb" aria-pressed={paused} onClick={() => setPaused(!paused)}>{paused ? <Play size={14} /> : <Pause size={14} />}<span>{paused ? "Resume" : "Pause"}</span></button>
      {!fileMode && <button className="tb" onClick={() => api("/api/signal-values/reset-peaks", "POST").catch(() => {})} title="Forget the lowest and highest values seen so far"><span>Reset peaks</span></button>}
      <span className="muted" style={{ marginLeft: "auto" }}>{vals.length} signals{fileMode ? " (at the end of the file)" : running ? "" : " (stopped)"}</span>
    </>
  );
  return (
    <Pane paneId="data" title="Data" tools={tools}>
      {vals.length === 0 ? (
        <div className="empty">{cat.length === 0 ? (fileMode ? "No database attached. Open a DBC file (Open file) and the signals in this recording get their names." : "No database loaded. Attach a DBC file on the Setup tab to see signal values.") : "No decoded frames yet."}</div>
      ) : (
        <table className="tbl">
          <thead><tr><th scope="col">Graph</th><th scope="col">Name</th><th scope="col">Value</th><th scope="col">Unit</th><th scope="col">Raw</th>{!fileMode && <><th scope="col">Lowest</th><th scope="col">Highest</th></>}<th scope="col">Bar</th></tr></thead>
          <tbody>
            {vals.map((r) => {
              const key = `${r.message}.${r.signal}`;
              const [lo, hi] = range.get(key) ?? [null, null];
              const num = typeof r.value === "number" ? r.value : null;
              const pct = num !== null && lo !== null && hi !== null && hi > lo ? Math.max(0, Math.min(100, ((num - lo) / (hi - lo)) * 100)) : null;
              return (
                <tr key={`${r.channel}-${key}`}>
                  <td><input type="checkbox" aria-label={`Plot ${key}`} checked={plotted.includes(key)} onChange={(e) => onPlot(key, e.target.checked)} /></td>
                  <td>{key}</td>
                  <td className="mono">{String(r.value)}</td>
                  <td>{r.unit}</td>
                  <td className="mono">{r.raw ?? ""}</td>
                  {!fileMode && <><td className="mono">{r.min ?? ""}</td><td className="mono">{r.max ?? ""}</td></>}
                  <td>{pct !== null && <div className="bar" role="presentation"><i style={{ width: `${pct}%` }} /></div>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}
    </Pane>
  );
}

/* ---------------------------------------------------------------- Graphic */
function css(name: string) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }

export function GraphicPane({ plotted, onPlot, fileKey }: { plotted: string[]; onPlot: (name: string, on: boolean) => void; fileKey?: string }) {
  const el = useRef<HTMLDivElement>(null);
  const plot = useRef<uPlot | null>(null);
  const [paused, setPaused] = useState(false);
  const [cat, setCat] = useState<CatalogueMsg[]>([]);
  const [hidden, setHidden] = useState<string[]>([]);
  const [data, setData] = useState<number[][]>([[]]);
  const [adding, setAdding] = useState("");

  const fileMode = fileKey !== undefined;
  useEffect(() => { api<CatalogueMsg[]>(fileMode ? "/api/file/signals" : "/api/signals").then(setCat).catch(() => {}); }, [fileMode, fileKey]);
  useEffect(() => { if (!fileMode) api("/api/watch", "PUT", { signals: plotted }).catch(() => {}); }, [plotted, fileMode]);

  useEffect(() => {
    if ((paused && !fileMode) || plotted.length === 0) return;
    const tick = async () => {
      try {
        const url = (n: string) => fileMode ? `/api/file/series?name=${encodeURIComponent(n)}` : `/api/signals/history?name=${encodeURIComponent(n)}`;
        // a signal that is not in this file simply plots as empty
        const series = await Promise.all(plotted.map((n) => api<[number, number][]>(url(n)).catch(() => [] as [number, number][])));
        const xs = Array.from(new Set(series.flatMap((s) => s.map((p) => p[0])))).sort((a, b) => a - b);
        const cols = series.map((s) => { const m = new Map(s); return xs.map((x) => (m.has(x) ? (m.get(x) as number) : null)); });
        setData([xs, ...cols] as unknown as number[][]);
      } catch { /* keep last */ }
    };
    tick();
    if (fileMode) return;                       // the whole recording is plotted once
    const t = window.setInterval(tick, 500);
    return () => window.clearInterval(t);
  }, [plotted, paused, fileMode, fileKey]);

  const colors = useMemo(() => [1, 2, 3, 4, 5, 6].map((i) => css(`--series-${i}`)), []);
  const dashes = [[], [6, 3], [2, 3], [8, 3, 2, 3], [], [6, 3]];

  useEffect(() => {
    if (!el.current) return;
    plot.current?.destroy();
    const axis = css("--color-text-muted"), grid = css("--color-border");
    const opts: uPlot.Options = {
      width: el.current.clientWidth || 300, height: el.current.clientHeight || 200,
      legend: { show: false }, cursor: { drag: { x: true, y: false } },
      scales: { x: { time: false } },
      axes: [{ stroke: axis, grid: { stroke: grid } }, { stroke: axis, grid: { stroke: grid }, size: 60 }],
      series: [{}, ...plotted.map((n, i) => ({ label: n, stroke: colors[i % 6], dash: dashes[i % 6], width: 1.5, show: !hidden.includes(n), spanGaps: true }))],
    };
    plot.current = new uPlot(opts, [[], ...plotted.map(() => [])] as uPlot.AlignedData, el.current);
    const ro = new ResizeObserver(() => el.current && plot.current?.setSize({ width: el.current.clientWidth, height: el.current.clientHeight }));
    ro.observe(el.current);
    return () => { ro.disconnect(); plot.current?.destroy(); plot.current = null; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [plotted, hidden]);

  useEffect(() => { if (plot.current && data.length === plotted.length + 1) plot.current.setData(data as uPlot.AlignedData); }, [data, plotted.length]);

  const all = cat.flatMap((c) => c.signals.map((s) => `${c.name}.${s.name}`)).filter((n) => !plotted.includes(n));
  const last = (i: number) => { const col = data[i + 1]; for (let k = (col?.length ?? 0) - 1; k >= 0; k--) if (col[k] != null) return col[k]; return undefined; };

  const tools = (
    <>
      <select aria-label="Add signal to graph" value={adding} onChange={(e) => { if (e.target.value) { onPlot(e.target.value, true); } setAdding(""); }}>
        <option value="">Add signal…</option>
        {all.map((n) => <option key={n} value={n}>{n}</option>)}
      </select>
      <button className="tb" aria-pressed={paused} onClick={() => setPaused(!paused)}>{paused ? <Play size={14} /> : <Pause size={14} />}<span>{paused ? "Resume" : "Pause"}</span></button>
      <span className="muted" style={{ marginLeft: "auto" }}>{fileMode ? "whole recording · " : ""}drag to zoom · double-click to reset</span>
    </>
  );
  return (
    <Pane paneId="graphic" title="Graphic" tools={tools}>
      {plotted.length === 0 ? (
        <div className="empty">No signals chosen. Tick “Graph” in the Data pane, or add one above.</div>
      ) : (
        <div className="gfx">
          <div className="list">
            <ul aria-label="Plotted signals">
              {plotted.map((n, i) => (
                <li key={n}>
                  <input type="checkbox" aria-label={`Show ${n}`} checked={!hidden.includes(n)} onChange={(e) => setHidden(e.target.checked ? hidden.filter((h) => h !== n) : [...hidden, n])} />
                  <span className="swatch" style={{ background: colors[i % 6] }} aria-hidden />
                  <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis" }}>{n}</span>
                  <span className="mono muted">{last(i) !== undefined ? Number(last(i)).toFixed(2) : ""}</span>
                  <button className="tb" aria-label={`Remove ${n}`} onClick={() => onPlot(n, false)}><X size={12} /></button>
                </li>
              ))}
            </ul>
          </div>
          <div className="plot" ref={el} />
        </div>
      )}
    </Pane>
  );
}

/* ------------------------------------------------------------ Bus statistic */
export function StatPane() {
  const lv = useLive();
  const names = Object.keys(lv.stats);
  return (
    <Pane paneId="stat" title="Bus statistic">
      {names.length === 0 ? <div className="empty">No channel configured.</div> : names.map((n) => {
        const s = lv.stats[n];
        const rows: [string, string][] = [
          ["Bus load [%]", s.load_pct.toFixed(2)], ["Peak load [%]", s.peak_load_pct.toFixed(2)],
          ["Rx frames", String(s.rx_frames)], ["Tx frames", String(s.tx_frames)],
          ["Rx rate [fr/s]", s.rx_rate.toFixed(0)], ["Tx rate [fr/s]", s.tx_rate.toFixed(0)],
          ["Rx bytes", String(s.rx_bytes)], ["Tx bytes", String(s.tx_bytes)],
          ["Error frames", String(s.error_frames)], ["Connection", lv.state?.running ? "Running" : "Stopped"],
        ];
        return (
          <div key={n}>
            <div style={{ padding: "2px 12px", fontWeight: 600 }}>Channel {n} <span className="muted">(load is an estimate, no bit stuffing)</span></div>
            <div className="kv">{rows.map(([k, v]) => <div key={k}><span>{k}</span><span className="v" style={k === "Error frames" && s.error_frames ? { color: "var(--color-danger)" } : undefined}>{v}</span></div>)}</div>
          </div>
        );
      })}
    </Pane>
  );
}

/* ------------------------------------------------------------------ Write */
export interface LogLine { ts: number; source: string; level: "info" | "warn" | "error"; text: string }
export function WritePane({ lines }: { lines: LogLine[] }) {
  const [tab, setTab] = useState<"All" | "System" | "MCP">("All");
  const shown = lines.filter((l) => tab === "All" || l.source === tab);
  return (
    <Pane paneId="write" title="Write" tools={
      <div role="tablist" aria-label="Source" style={{ display: "flex", gap: 2 }}>
        {(["All", "System", "MCP"] as const).map((t) => <button key={t} role="tab" aria-selected={tab === t} className="tb" onClick={() => setTab(t)}>{t}</button>)}
      </div>}>
      {shown.length === 0 ? <div className="empty">Nothing written yet.</div> : (
        <table className="tbl" aria-live="polite">
          <thead><tr><th scope="col">Time</th><th scope="col">Source</th><th scope="col">Message</th></tr></thead>
          <tbody>
            {[...shown].reverse().map((l, i) => (
              <tr key={i}>
                <td className="mono">{new Date(l.ts).toLocaleTimeString()}</td>
                <td>{l.source}</td>
                <td style={{ color: l.level === "error" ? "var(--color-danger)" : l.level === "warn" ? "var(--color-warning)" : undefined }}>
                  {l.level === "error" ? "Error: " : l.level === "warn" ? "Warning: " : ""}{l.text}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Pane>
  );
}


