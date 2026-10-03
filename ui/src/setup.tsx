import { useCallback, useEffect, useState } from "react";
import { Plus, Trash2, Send, Square, Play, Circle } from "lucide-react";
import { api, type BusState, type Channel, type Filter } from "./api";
import { parseId } from "./format";
import { Field, Modal, type Notify } from "./ui";

interface Adapter { interface: string; channel: string; label: string }
const BITRATES = [125000, 250000, 500000, 1000000];

/* ---------------------------------------------------------------- channels */
function ChannelDialog({ open, initial, onClose, onSaved, notify }: { open: boolean; initial?: Channel; onClose: () => void; onSaved: () => void; notify: Notify }) {
  const [adapters, setAdapters] = useState<Adapter[]>([]);
  const [f, setF] = useState({ name: "can1", adapter: "virtual|vcan0", bitrate: 500000, fd: false, fdRate: 2000000, listenOnly: true });
  const [err, setErr] = useState("");
  useEffect(() => {
    if (!open) return;
    api<Adapter[]>("/api/adapters").then(setAdapters).catch(() => {});
    setErr("");
    if (initial) setF({ name: initial.name, adapter: `${initial.interface}|${initial.channel}`, bitrate: initial.bitrate, fd: initial.fd_enabled, fdRate: initial.fd_data_bitrate ?? 2000000, listenOnly: initial.listen_only });
  }, [open, initial]);
  const save = async () => {
    if (!f.name.trim()) return setErr("Give the channel a name.");
    const [iface, chan] = f.adapter.split("|");
    try {
      await api("/api/channels", "PUT", { name: f.name.trim(), interface: iface, channel: chan, bitrate: f.bitrate, fd_enabled: f.fd, fd_data_bitrate: f.fd ? f.fdRate : null, listen_only: f.listenOnly });
      onSaved(); onClose();
    } catch (e) { setErr((e as Error).message); notify((e as Error).message, "error"); }
  };
  const options = adapters.length ? adapters : [{ interface: "virtual", channel: "vcan0", label: "Virtual bus (no hardware)" }];
  return (
    <Modal title={initial ? `Channel ${initial.name}` : "Add channel"} open={open} onClose={onClose}
      footer={<><button className="btn" onClick={onClose}>Cancel</button><button className="btn primary" onClick={save}>Save</button></>}>
      <Field label="Name"><input type="text" value={f.name} disabled={!!initial} onChange={(e) => setF({ ...f, name: e.target.value })} /></Field>
      <Field label="Adapter">
        <select value={f.adapter} onChange={(e) => setF({ ...f, adapter: e.target.value })}>
          {options.map((a) => <option key={`${a.interface}|${a.channel}`} value={`${a.interface}|${a.channel}`}>{a.label}</option>)}
        </select>
      </Field>
      <div className="row">
        <Field label="Bitrate [bit/s]">
          <select value={f.bitrate} onChange={(e) => setF({ ...f, bitrate: Number(e.target.value) })}>
            {BITRATES.map((b) => <option key={b} value={b}>{b.toLocaleString()}</option>)}
          </select>
        </Field>
        <label className="fixed"><input type="checkbox" checked={f.fd} onChange={(e) => setF({ ...f, fd: e.target.checked })} /> CAN FD</label>
        {f.fd && <Field label="Data bitrate [bit/s]"><input type="number" min={1} value={f.fdRate} onChange={(e) => setF({ ...f, fdRate: Number(e.target.value) })} /></Field>}
      </div>
      <label><input type="checkbox" checked={f.listenOnly} onChange={(e) => setF({ ...f, listenOnly: e.target.checked })} /> Listen-only (blocks all transmitting on this channel)</label>
      {!f.listenOnly && <p className="muted" style={{ margin: 0 }}>Transmitting onto a real vehicle bus can affect real equipment. Only turn this off when you mean to send.</p>}
      {err && <div className="err-msg" role="alert">{err}</div>}
    </Modal>
  );
}

export function SetupPage({ channels, state, refresh, notify }: { channels: Channel[]; state: BusState | null; refresh: () => void; notify: Notify }) {
  const [dlg, setDlg] = useState<{ open: boolean; ch?: Channel }>({ open: false });
  const running = !!state?.running;
  const act = useCallback(async (fn: () => Promise<unknown>, ok?: string) => {
    try { await fn(); if (ok) notify(ok); refresh(); } catch (e) { notify((e as Error).message, "error"); }
  }, [notify, refresh]);

  return (
    <div className="setup">
      <section className="card" aria-labelledby="h-ch">
        <h3 id="h-ch">Channels</h3>
        <div className="cb">
          {channels.length === 0 ? <div className="empty">No channel yet. Add one to choose an adapter and bitrate.</div> : (
            <table className="tbl">
              <thead><tr><th scope="col">Name</th><th scope="col">Adapter</th><th scope="col">Bitrate</th><th scope="col">FD</th><th scope="col">Mode</th><th /></tr></thead>
              <tbody>
                {channels.map((c) => (
                  <tr key={c.name}>
                    <td className="wrap" title={c.name}>{c.name}</td><td>{c.interface}:{c.channel}</td><td>{c.bitrate.toLocaleString()}</td>
                    <td>{c.fd_enabled ? `on (${c.fd_data_bitrate?.toLocaleString()})` : "off"}</td>
                    <td>{c.listen_only ? "listen-only" : "can transmit"}</td>
                    <td style={{ textAlign: "right" }}>
                      <button className="btn" disabled={running} onClick={() => setDlg({ open: true, ch: c })}>Edit</button>{" "}
                      <button className="btn danger" disabled={running} aria-label={`Remove ${c.name}`} onClick={() => act(() => api(`/api/channels/${c.name}`, "DELETE"))}><Trash2 size={13} /></button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          <div><button className="btn" disabled={running} onClick={() => setDlg({ open: true })}><Plus size={13} /> Add channel</button>{running && <span className="muted"> Stop the measurement to change channels.</span>}</div>
        </div>
      </section>

      <DatabaseCard channels={channels} act={act} />
      <FilterCard channels={channels} act={act} />
      <TransmitCard channels={channels} state={state} act={act} />
      <LoggingCard state={state} act={act} />
      <ReplayCard channels={channels} state={state} act={act} />
      <ChannelDialog open={dlg.open} initial={dlg.ch} onClose={() => setDlg({ open: false })} onSaved={refresh} notify={notify} />
    </div>
  );
}

type Act = (fn: () => Promise<unknown>, ok?: string) => Promise<void>;

/* --------------------------------------------------------------- databases */
function DatabaseCard({ channels, act }: { channels: Channel[]; act: Act }) {
  const [path, setPath] = useState("");
  const [ch, setCh] = useState("");
  const target = ch || channels[0]?.name || "";
  return (
    <section className="card" aria-labelledby="h-db">
      <h3 id="h-db">Databases</h3>
      <div className="cb">
        {channels.every((c) => c.databases.length === 0) ? <div className="empty">No database attached. Frames show as raw bytes until you attach a DBC.</div> :
          channels.flatMap((c) => c.databases.map((d) => (
            <div key={`${c.name}${d}`} className="row"><span><b>{c.name}</b> {d}</span>
              <button className="btn fixed" onClick={() => act(() => api(`/api/databases/${c.name}`, "DELETE"))}>Detach</button></div>)))}
        <div className="row">
          <Field label="DBC file path"><input type="text" value={path} placeholder="C:\path\to\file.dbc" onChange={(e) => setPath(e.target.value)} /></Field>
          <Field label="Channel"><select value={target} onChange={(e) => setCh(e.target.value)}>{channels.map((c) => <option key={c.name}>{c.name}</option>)}</select></Field>
          <button className="btn fixed" disabled={!path || !target} onClick={() => act(async () => { await api("/api/databases", "POST", { channel: target, path }); setPath(""); }, "Database loaded")}>Attach</button>
        </div>
      </div>
    </section>
  );
}

/* ----------------------------------------------------------------- filters */
function FilterCard({ channels, act }: { channels: Channel[]; act: Act }) {
  const [list, setList] = useState<Filter[]>([]);
  const [f, setF] = useState({ mode: "pass", from: "", to: "", ext: false, dir: "both", ch: "" });
  const [err, setErr] = useState("");
  const load = useCallback(() => api<Filter[]>("/api/filters").then(setList).catch(() => {}), []);
  useEffect(() => { load(); }, [load]);
  const add = async () => {
    const a = parseId(f.from), b = parseId(f.to || f.from);
    if (a === null || b === null) return setErr("Enter IDs as decimal or 0x hex.");
    if (b < a) return setErr("The end ID must not be below the start ID.");
    setErr("");
    await act(async () => { await api("/api/filters", "PUT", { mode: f.mode, id_from: a, id_to: b, ext: f.ext, direction: f.dir, channel: f.ch || null }); await load(); });
  };
  return (
    <section className="card" aria-labelledby="h-fl">
      <h3 id="h-fl">Filters <span className="muted" style={{ fontWeight: 400 }}>(affect Trace and MCP reads, not statistics or logs)</span></h3>
      <div className="cb">
        {list.length === 0 ? <div className="empty">No filters: every frame is shown.</div> : list.map((r) => (
          <div key={r.id} className="row">
            <span><b>{r.mode}</b> 0x{r.id_from.toString(16)}–0x{r.id_to.toString(16)} {r.ext ? "ext" : "std"} {r.direction} {r.channel ?? "all channels"}</span>
            <label className="fixed"><input type="checkbox" checked={r.enabled} onChange={(e) => act(async () => { await api("/api/filters", "PUT", { ...r, enabled: e.target.checked }); await load(); })} /> on</label>
            <button className="btn fixed" aria-label="Remove filter" onClick={() => act(async () => { await api(`/api/filters/${r.id}`, "DELETE"); await load(); })}><Trash2 size={13} /></button>
          </div>))}
        <div className="row">
          <Field label="Mode"><select value={f.mode} onChange={(e) => setF({ ...f, mode: e.target.value })}><option value="pass">Pass</option><option value="stop">Stop</option></select></Field>
          <Field label="From ID"><input type="text" value={f.from} onChange={(e) => setF({ ...f, from: e.target.value })} aria-invalid={!!err} /></Field>
          <Field label="To ID"><input type="text" value={f.to} onChange={(e) => setF({ ...f, to: e.target.value })} /></Field>
          <Field label="Direction"><select value={f.dir} onChange={(e) => setF({ ...f, dir: e.target.value })}><option value="both">Rx + Tx</option><option value="rx">Rx</option><option value="tx">Tx</option></select></Field>
          <Field label="Channel"><select value={f.ch} onChange={(e) => setF({ ...f, ch: e.target.value })}><option value="">All</option>{channels.map((c) => <option key={c.name}>{c.name}</option>)}</select></Field>
          <label className="fixed"><input type="checkbox" checked={f.ext} onChange={(e) => setF({ ...f, ext: e.target.checked })} /> Extended</label>
          <button className="btn fixed" onClick={add}>Add</button>
        </div>
        {err && <div className="err-msg" role="alert">{err}</div>}
      </div>
    </section>
  );
}

/* ---------------------------------------------------------------- transmit */
function TransmitCard({ channels, state, act }: { channels: Channel[]; state: BusState | null; act: Act }) {
  const [f, setF] = useState({ ch: "", id: "0x123", data: "01 02 03 04", period: "100", ext: false, fd: false });
  const [err, setErr] = useState("");
  const running = !!state?.running;
  const target = f.ch || channels[0]?.name || "";
  const cfg = channels.find((c) => c.name === target);
  const body = () => {
    const id = parseId(f.id);
    if (id === null) { setErr("Enter the ID as decimal or 0x hex."); return null; }
    if (!/^([0-9a-fA-F]{2}\s*)*$/.test(f.data.trim())) { setErr("Data must be hex bytes, like 01 02 FF."); return null; }
    setErr("");
    return { channel: target, id, data: f.data, ext: f.ext, fd: f.fd };
  };
  return (
    <section className="card" aria-labelledby="h-tx">
      <h3 id="h-tx">Transmit</h3>
      <div className="cb">
        {cfg?.listen_only && <div className="empty">Channel {target} is listen-only. Edit the channel and turn that off to transmit.</div>}
        {!running && <div className="muted">Start the measurement to transmit.</div>}
        <div className="row">
          <Field label="Channel"><select value={target} onChange={(e) => setF({ ...f, ch: e.target.value })}>{channels.map((c) => <option key={c.name}>{c.name}</option>)}</select></Field>
          <Field label="ID"><input type="text" value={f.id} onChange={(e) => setF({ ...f, id: e.target.value })} aria-invalid={!!err} /></Field>
          <Field label="Data (hex)"><input type="text" className="mono" value={f.data} onChange={(e) => setF({ ...f, data: e.target.value })} /></Field>
          <Field label="Period [ms]"><input type="number" min={1} value={f.period} onChange={(e) => setF({ ...f, period: e.target.value })} /></Field>
          <label className="fixed"><input type="checkbox" checked={f.ext} onChange={(e) => setF({ ...f, ext: e.target.checked })} /> Ext</label>
          <label className="fixed"><input type="checkbox" checked={f.fd} onChange={(e) => setF({ ...f, fd: e.target.checked })} /> FD</label>
        </div>
        {err && <div className="err-msg" role="alert">{err}</div>}
        <div className="row">
          <button className="btn fixed" disabled={!running} onClick={() => { const b = body(); if (b) act(() => api("/api/transmit/once", "POST", b)); }}><Send size={13} /> Send once</button>
          <button className="btn fixed" disabled={!running} onClick={() => { const b = body(); if (b) act(() => api("/api/transmit/cyclic", "POST", { ...b, period_ms: Number(f.period) }), "Cyclic transmit started"); }}><Play size={13} /> Start cyclic</button>
        </div>
        {(state?.cyclic ?? []).map((c) => (
          <div key={c.id} className="row"><span>Every {c.period_ms} ms: {c.channel} 0x{c.can_id.toString(16)} [{c.data}]</span>
            <button className="btn fixed" onClick={() => act(() => api(`/api/transmit/cyclic/${c.id}`, "DELETE"))}><Square size={13} /> Stop</button></div>))}
      </div>
    </section>
  );
}

/* ----------------------------------------------------------------- logging */
function LoggingCard({ state, act }: { state: BusState | null; act: Act }) {
  const [path, setPath] = useState("");
  const [fmt, setFmt] = useState("asc");
  const running = !!state?.running;
  const lg = state?.logging;
  return (
    <section className="card" aria-labelledby="h-lg">
      <h3 id="h-lg">Logging</h3>
      <div className="cb">
        {lg ? (
          <div className="row"><span><Circle size={11} fill="var(--color-danger)" color="var(--color-danger)" /> Logging {lg.frames.toLocaleString()} frames to {lg.path}</span>
            <button className="btn fixed" onClick={() => act(() => api("/api/log/stop", "POST"), "Log saved")}><Square size={13} /> Stop</button></div>
        ) : (
          <>
            {!running && <div className="muted">Start the measurement to log.</div>}
            <div className="row">
              <Field label="File path"><input type="text" value={path} placeholder="C:\logs\drive1.asc" onChange={(e) => setPath(e.target.value)} /></Field>
              <Field label="Format"><select value={fmt} onChange={(e) => setFmt(e.target.value)}><option value="asc">ASC (text)</option><option value="blf">BLF (binary)</option></select></Field>
              <button className="btn fixed" disabled={!running || !path} onClick={() => act(() => api("/api/log/start", "POST", { path, format: fmt }), "Logging started")}><Circle size={13} /> Start</button>
            </div>
          </>
        )}
      </div>
    </section>
  );
}

/* ------------------------------------------------------------------ replay */
function ReplayCard({ channels, state, act }: { channels: Channel[]; state: BusState | null; act: Act }) {
  const [f, setF] = useState({ path: "", speed: "1", loop: false, ch: "" });
  const running = !!state?.running;
  const rp = state?.replay;
  return (
    <section className="card" aria-labelledby="h-rp">
      <h3 id="h-rp">Replay</h3>
      <div className="cb">
        {rp ? (
          <div className="row"><span>Replaying {rp.path}: {rp.position.toLocaleString()} frames</span>
            <button className="btn fixed" onClick={() => act(() => api("/api/replay/stop", "POST"))}><Square size={13} /> Stop</button></div>
        ) : (
          <>
            {!running && <div className="muted">Start the measurement first. With no hardware, replay shows the frames in Trace only (offline).</div>}
            <div className="row">
              <Field label="Log file (ASC or BLF)"><input type="text" value={f.path} placeholder="C:\logs\drive1.asc" onChange={(e) => setF({ ...f, path: e.target.value })} /></Field>
              <Field label="Speed ×"><input type="number" min={0.1} step={0.1} value={f.speed} onChange={(e) => setF({ ...f, speed: e.target.value })} /></Field>
              <Field label="Channel"><select value={f.ch} onChange={(e) => setF({ ...f, ch: e.target.value })}><option value="">Default</option>{channels.map((c) => <option key={c.name}>{c.name}</option>)}</select></Field>
              <label className="fixed"><input type="checkbox" checked={f.loop} onChange={(e) => setF({ ...f, loop: e.target.checked })} /> Loop</label>
              <button className="btn fixed" disabled={!running || !f.path} onClick={() => act(() => api("/api/replay/start", "POST", { path: f.path, channel: f.ch || null, speed: Number(f.speed), loop: f.loop }), "Replay started")}><Play size={13} /> Start</button>
            </div>
          </>
        )}
      </div>
    </section>
  );
}
