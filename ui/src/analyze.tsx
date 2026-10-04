import { useEffect, useState } from "react";
import { Download, RefreshCw, Search, Trash2 } from "lucide-react";
import { api, token, type FileSummary, type Frame } from "./api";
import { Field, Pane, type Notify } from "./ui";

interface Row { channel: string; id: number; ext: boolean; dir: "rx" | "tx"; count: number; mean_ms: number | null; sd_ms: number | null; min_ms: number | null; max_ms: number | null }
interface Saved { name: string; size: number; modified: number }

const ms = (v: number | null) => (v === null ? "—" : v.toLocaleString(undefined, { maximumFractionDigits: 2 }));
const hex = (id: number, ext: boolean) => `0x${id.toString(16).toUpperCase().padStart(ext ? 8 : 3, "0")}`;
const COND_HELP = "Examples: id == 0x100    id > 0x100 and d0 == 5    error == 1    time > 12.5. You can use id, dlc, time, dir, error and d0 to d63. Join with 'and' or 'or'.";

async function download(name: string) {
  const res = await fetch(`/api/exports/${encodeURIComponent(name)}`, { headers: { Authorization: `Bearer ${token}` } });
  if (!res.ok) throw new Error("Could not download that file.");
  const url = URL.createObjectURL(await res.blob());
  const a = document.createElement("a");
  a.href = url; a.download = name; a.click();
  URL.revokeObjectURL(url);
}

/** Statistics report, search in an open file, and saving data to other formats. */
export function AnalyzePage({ file, notify }: { file: FileSummary | null; notify: Notify }) {
  const [rep, setRep] = useState<{ source: string; frames: number; rows: Row[] } | null>(null);
  const loadRep = () => { api<{ source: string; frames: number; rows: Row[] }>("/api/statistics/report").then(setRep).catch((e) => notify((e as Error).message, "error")); };
  useEffect(loadRep, [file?.name, file?.frames]);   // eslint-disable-line react-hooks/exhaustive-deps

  // ---- search
  const [cond, setCond] = useState("");
  const [found, setFound] = useState<{ index: number | null; matches: number; frame?: Frame; asked: boolean } | null>(null);
  const [from, setFrom] = useState(0);
  const find = async (start: number) => {
    try {
      const r = await api<{ index: number | null; matches: number }>(`/api/file/find?cond=${encodeURIComponent(cond)}&start=${start}`);
      let frame: Frame | undefined;
      if (r.index !== null) frame = (await api<{ frames: Frame[] }>(`/api/file/frames?offset=${r.index}&limit=1`)).frames[0];
      setFound({ ...r, frame, asked: true });
      setFrom(r.index !== null ? r.index + 1 : 0);
    } catch (e) { notify((e as Error).message, "error"); }
  };

  // ---- save
  const [fmt, setFmt] = useState("csv");
  const [name, setName] = useState("my-data");
  const [keep, setKeep] = useState("");
  const [trig, setTrig] = useState("");
  const [trigEnd, setTrigEnd] = useState("");
  const [pre, setPre] = useState("0.5");
  const [post, setPost] = useState("0.5");
  const [sigs, setSigs] = useState("");
  const [saved, setSaved] = useState<Saved[]>([]);
  const loadSaved = () => { api<Saved[]>("/api/exports").then(setSaved).catch(() => {}); };
  useEffect(loadSaved, []);
  const save = async () => {
    try {
      const body = fmt === "signals"
        ? { name, format: "signals", signals: sigs.split(/[,\n]/).map((x) => x.trim()).filter(Boolean) }
        : { name, format: fmt, condition: keep || null, trigger: trig || null, trigger_end: trig && trigEnd ? trigEnd : null, pre: Number(pre) || 0, post: Number(post) || 0 };
      const r = await api<{ name: string; frames?: number; rows?: number }>("/api/export", "POST", body);
      notify(`Saved ${r.name} (${(r.frames ?? r.rows ?? 0).toLocaleString()} ${r.frames !== undefined ? "frames" : "rows"}).`);
      loadSaved();
    } catch (e) { notify((e as Error).message, "error"); }
  };

  const source = rep?.source === "file" ? `the open file (${file?.name ?? ""})` : "what is on the bus right now";
  return (
    <div className="setup">
      <Pane className="wide" title="Statistics report" tools={<button className="tb" onClick={loadRep}><RefreshCw size={14} /><span>Refresh</span></button>}>
        <p className="muted" style={{ margin: "0 0 8px" }}>How often each message arrives, from {source}. Spacing is the time between two of the same message in a row; a high spread means an uneven sender.</p>
        {!rep || rep.rows.length === 0 ? <div className="empty">No frames yet. Start a measurement or open a file, then press Refresh.</div> : (
          <table className="tbl">
            <thead><tr><th scope="col">Channel</th><th scope="col">ID</th><th scope="col">Dir</th><th scope="col">Frames</th><th scope="col">Average spacing (ms)</th><th scope="col">Spread (ms)</th><th scope="col">Shortest (ms)</th><th scope="col">Longest (ms)</th></tr></thead>
            <tbody>
              {rep.rows.map((r) => (
                <tr key={`${r.channel}${r.id}${r.ext}${r.dir}`}>
                  <td>{r.channel}</td><td className="mono">{hex(r.id, r.ext)}</td><td>{r.dir}</td><td className="mono">{r.count.toLocaleString()}</td>
                  <td className="mono">{ms(r.mean_ms)}</td><td className="mono">{ms(r.sd_ms)}</td><td className="mono">{ms(r.min_ms)}</td><td className="mono">{ms(r.max_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Pane>

      <section className="card" aria-labelledby="h-find">
        <h3 id="h-find">Find a frame</h3>
        <div className="cb">
          {!file ? <div className="empty">Open a recording first (Open file). Then you can search it here.</div> : (
            <>
              <Field label="Look for frames where"><input value={cond} onChange={(e) => { setCond(e.target.value); setFound(null); setFrom(0); }} placeholder="id > 0x100 and d0 == 5" /></Field>
              <p className="muted" style={{ margin: 0 }}>{COND_HELP}</p>
              <div><button className="btn" disabled={!cond.trim()} onClick={() => find(from)}><Search size={13} /> {from > 0 ? "Find next" : "Find"}</button></div>
              {found?.asked && (found.index === null
                ? <div role="status">{found.matches === 0 ? "No frame matches that." : `No more matches after this point (${found.matches.toLocaleString()} in total).`}</div>
                : <div role="status">Match at <b>{found.frame?.ts.toFixed(3)} s</b>: {found.frame ? <span className="mono">{hex(found.frame.id, found.frame.ext)} {found.frame.data.match(/../g)?.join(" ").toUpperCase()}</span> : null} ({found.matches.toLocaleString()} matches in total)</div>)}
            </>
          )}
        </div>
      </section>

      <section className="card" aria-labelledby="h-save">
        <h3 id="h-save">Save or convert</h3>
        <div className="cb">
          <p className="muted" style={{ margin: 0 }}>Save {rep?.source === "file" ? "the open file" : "what is on the bus right now"} in another format, or only a part of it. Saved files can be downloaded and are kept in your busscript-data folder.</p>
          <Field label="Save as">
            <select value={fmt} onChange={(e) => setFmt(e.target.value)}>
              <option value="csv">Spreadsheet table (CSV)</option>
              <option value="asc">Vector ASC</option>
              <option value="blf">Vector BLF</option>
              <option value="mf4">MDF4 (MF4)</option>
              <option value="signals" disabled={!file}>Signal values for a spreadsheet (needs an open file and a database)</option>
            </select>
          </Field>
          <Field label="File name"><input value={name} onChange={(e) => setName(e.target.value)} /></Field>
          {fmt === "signals" ? (
            <Field label="Signals (Message.Signal, separated by commas)"><input value={sigs} onChange={(e) => setSigs(e.target.value)} placeholder="EngineData.EngineSpeed, VehicleSpeed.Speed" /></Field>
          ) : (
            <>
              <Field label="Only keep frames where (optional)"><input value={keep} onChange={(e) => setKeep(e.target.value)} placeholder="id == 0x100" /></Field>
              <Field label="Only keep the time around frames where (optional)"><input value={trig} onChange={(e) => setTrig(e.target.value)} placeholder="error == 1" /></Field>
              {trig && (
                <>
                  <Field label="…until a frame where (optional, otherwise just around each one)"><input value={trigEnd} onChange={(e) => setTrigEnd(e.target.value)} placeholder="id == 0x7FF" /></Field>
                  <div className="row">
                    <Field label="Seconds before"><input type="number" min={0} step={0.1} value={pre} onChange={(e) => setPre(e.target.value)} /></Field>
                    <Field label="Seconds after"><input type="number" min={0} step={0.1} value={post} onChange={(e) => setPost(e.target.value)} /></Field>
                  </div>
                </>
              )}
              <p className="muted" style={{ margin: 0 }}>{COND_HELP}</p>
            </>
          )}
          <div><button className="btn primary" onClick={save}><Download size={13} /> Save</button></div>
          {saved.length > 0 && (
            <table className="tbl">
              <caption className="muted" style={{ textAlign: "left" }}>Saved files</caption>
              <tbody>
                {saved.map((s) => (
                  <tr key={s.name}>
                    <td className="wrap">{s.name}</td><td className="mono">{(s.size / 1024).toFixed(0)} KB</td>
                    <td style={{ textAlign: "right" }}>
                      <button className="btn" onClick={() => download(s.name).catch((e) => notify((e as Error).message, "error"))}><Download size={13} /> Download</button>{" "}
                      <button className="btn danger" aria-label={`Delete ${s.name}`} onClick={() => api(`/api/exports/${encodeURIComponent(s.name)}`, "DELETE").then(loadSaved)}><Trash2 size={13} /></button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>
    </div>
  );
}
