import { useEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { FileText } from "lucide-react";
import { api, type FileSummary, type Frame, type OverviewRow } from "./api";
import { bytesDec, decId, hexId, spaced, timeS } from "./format";
import { Pane } from "./ui";

const BLOCK = 500;
const ROW = 17;
type Decoded = { message: string; signal: string; value: unknown; unit: string }[];

/** The Trace for an opened file: rows are fetched from the server a block at a time as you scroll. */
export function FileTracePane({ info, hex, rev }: { info: FileSummary; hex: boolean; rev: number }) {
  const [overview, setOverview] = useState<OverviewRow[]>([]);
  const [filter, setFilter] = useState("");                 // "" = every message, else "id:ext"
  const [, bump] = useState(0);
  const [sel, setSel] = useState<number | null>(null);
  const [decoded, setDecoded] = useState<Decoded | null>(null);
  const [jump, setJump] = useState("");
  const cache = useRef(new Map<string, Frame[]>());
  const pending = useRef(new Set<string>());
  const parent = useRef<HTMLDivElement>(null);

  const key = `${info.name}|${info.frames}|${rev}|${filter}`;
  const [fid, fext] = filter ? filter.split(":") : [null, null];
  const query = filter ? `&id=${fid}&ext=${fext}` : "";

  useEffect(() => { api<OverviewRow[]>("/api/file/overview").then(setOverview).catch(() => {}); }, [info.name, info.frames, rev]);
  useEffect(() => { cache.current.clear(); pending.current.clear(); setSel(null); setDecoded(null); bump((n) => n + 1); }, [key]);

  const total = useMemo(() => {
    if (!filter) return info.frames;
    return overview.find((o) => `${o.id}:${o.ext}` === filter)?.count ?? 0;
  }, [filter, overview, info.frames]);

  const v = useVirtualizer({ count: total, getScrollElement: () => parent.current, estimateSize: () => ROW, overscan: 30 });
  const items = v.getVirtualItems();
  const firstIdx = items[0]?.index ?? 0;
  const lastIdx = items[items.length - 1]?.index ?? 0;

  useEffect(() => {
    for (let b = Math.floor(firstIdx / BLOCK); b <= Math.floor(lastIdx / BLOCK); b++) {
      const k = `${key}|${b}`;
      if (cache.current.has(k) || pending.current.has(k) || total === 0) continue;
      pending.current.add(k);
      api<{ frames: Frame[] }>(`/api/file/frames?offset=${b * BLOCK}&limit=${BLOCK}${query}`)
        .then((r) => { cache.current.set(k, r.frames); bump((n) => n + 1); })
        .catch(() => {})
        .finally(() => pending.current.delete(k));
    }
  }, [firstIdx, lastIdx, key, total, query]);

  const rowAt = (i: number) => cache.current.get(`${key}|${Math.floor(i / BLOCK)}`)?.[i % BLOCK];

  const pick = async (i: number, f: Frame) => {
    setSel(i);
    try { setDecoded(await api<Decoded>(`/api/frames/decode?channel=${encodeURIComponent(f.ch)}&id=${f.id}&ext=${f.ext}&data=${f.data}`)); }
    catch { setDecoded([]); }
  };

  const go = async () => {
    const t = Number(jump);
    if (jump.trim() === "" || !Number.isFinite(t) || t < 0) return;
    try {
      const r = await api<{ index: number }>(`/api/file/seek?t=${t}${query}`);
      const i = Math.min(r.index, Math.max(0, total - 1));
      v.scrollToIndex(i, { align: "start" });
      setSel(i);
    } catch { /* ignore */ }
  };

  const tools = (
    <>
      <label className="inline">Show
        <select aria-label="Show which messages" value={filter} onChange={(e) => setFilter(e.target.value)}>
          <option value="">All messages</option>
          {overview.map((o) => (
            <option key={`${o.id}:${o.ext}`} value={`${o.id}:${o.ext}`}>
              {(o.name ?? "Unknown")} (0x{o.id.toString(16).toUpperCase()}) · {o.count.toLocaleString()}
            </option>
          ))}
        </select>
      </label>
      <label className="inline">Go to
        <input type="text" inputMode="decimal" aria-label="Go to time in seconds" placeholder="seconds" value={jump} style={{ width: 70 }}
          onChange={(e) => setJump(e.target.value)} onKeyDown={(e) => e.key === "Enter" && go()} />
      </label>
      <button className="tb" onClick={go}>Go</button>
      <span className="muted" style={{ marginLeft: "auto" }}>{total.toLocaleString()}{filter ? ` of ${info.frames.toLocaleString()}` : ""} frames</span>
    </>
  );

  return (
    <Pane title={`Trace: ${info.name}`} icon={<FileText size={14} />} tools={tools}>
      <div style={{ display: "flex", flexDirection: "column", height: "100%" }}>
        <div ref={parent} style={{ flex: 1, minHeight: 0, overflow: "auto" }} role={total > 0 ? "grid" : undefined}
          aria-rowcount={total > 0 ? total : undefined} aria-label={total > 0 ? "Frames in the file" : undefined}>
          {total === 0 ? <div className="empty">No frames match.</div> : (
            <div className="vtable" style={{ height: v.getTotalSize() + 20 }}>
              <div className="vhead" role="row">
                {["Time [s]", "Chn", "Dir", "ID", "Name", "DLC", "Data"].map((h) => <span key={h} role="columnheader">{h}</span>)}
              </div>
              {items.map((it) => {
                const f = rowAt(it.index);
                return (
                  <div key={it.index} className={`vrow ${f?.dir === "tx" ? "tx" : ""} ${f?.error ? "err" : ""} ${sel === it.index ? "sel" : ""}`}
                    style={{ top: 20 + it.start }} role="row" tabIndex={0} aria-rowindex={it.index + 1}
                    onClick={() => f && pick(it.index, f)} onKeyDown={(e) => e.key === "Enter" && f && pick(it.index, f)}>
                    {f ? (
                      <>
                        <span role="gridcell" className="mono">{timeS(f.ts)}</span>
                        <span role="gridcell">{f.ch}</span>
                        <span role="gridcell">{f.error ? "ERR" : f.dir === "tx" ? "Tx" : "Rx"}</span>
                        <span role="gridcell" className="mono">{f.error ? "" : hex ? hexId(f) : decId(f)}</span>
                        <span role="gridcell">{f.name ?? (f.error ? "Error frame" : "—")}</span>
                        <span role="gridcell" className="mono">{f.dlc}</span>
                        <span role="gridcell" className="mono">{hex ? spaced(f.data) : bytesDec(f.data)}</span>
                      </>
                    ) : <span role="gridcell" className="muted">Loading…</span>}
                  </div>
                );
              })}
            </div>
          )}
        </div>
        {decoded && sel !== null && (
          <div className="decoded" style={{ position: "static", flex: "none" }} aria-live="polite">
            {decoded.length === 0 ? <span className="muted">No database entry for this ID. Attach a database file to see signal values.</span>
              : decoded.map((s) => <span key={s.signal} style={{ marginRight: 16 }}>{s.signal} = <b>{String(s.value)}</b> {s.unit}</span>)}
          </div>
        )}
      </div>
    </Pane>
  );
}

/** Summary of the open file and a table of every message in it. */
export function FileInfoPane({ info, rev }: { info: FileSummary; rev: number }) {
  const [rows, setRows] = useState<OverviewRow[]>([]);
  useEffect(() => { api<OverviewRow[]>("/api/file/overview").then(setRows).catch(() => {}); }, [info.name, info.frames, rev]);
  const mins = Math.floor(info.duration / 60);
  const dur = mins > 0 ? `${mins} min ${(info.duration - mins * 60).toFixed(0)} s` : `${info.duration.toFixed(2)} s`;
  const facts: [string, string][] = [
    ["File", info.name], ["Type", info.format], ["Frames", info.frames.toLocaleString() + (info.truncated ? " (first part only)" : "")],
    ["Length", dur], ["Channels", info.channels.join(", ") || "—"], ["Different messages", String(info.ids)],
    ["Error frames", String(info.error_frames)],
  ];
  return (
    <Pane title="File">
      <div className="kv" style={{ gridTemplateColumns: "1fr" }}>
        {facts.map(([k, val]) => <div key={k}><span>{k}</span><span className="v" style={{ textAlign: "right", wordBreak: "break-all" }}>{val}</span></div>)}
      </div>
      <table className="tbl">
        <thead><tr><th scope="col">ID</th><th scope="col">Name</th><th scope="col">Frames</th><th scope="col">Per second</th></tr></thead>
        <tbody>
          {rows.map((o) => (
            <tr key={`${o.id}:${o.ext}`}>
              <td className="mono">0x{o.id.toString(16).toUpperCase()}</td>
              <td>{o.name ?? <span className="muted">unknown</span>}</td>
              <td className="mono">{o.count.toLocaleString()}</td>
              <td className="mono">{o.rate ?? "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </Pane>
  );
}
