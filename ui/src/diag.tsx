import { useEffect, useState } from "react";
import { RefreshCw } from "lucide-react";
import { api } from "./api";
import { Pane } from "./ui";

interface Doctor {
  python: string; ready: boolean; problems: string[]; data_dir: string; tip: string;
  libraries: { name: string; why: string; required: boolean; version: string | null; ok: boolean; fix: string | null }[];
  drivers: { name: string; interface: string; found: boolean; path: string | null; fix: string | null }[];
  serial_ports: { port: string; label: string }[];
}
interface DiagMsg { ts: number; ch: string; id: number; ext: boolean; length: number; data: string; text: string; details: string[] }

/** What this PC has for talking to a real adapter, in plain words. */
export function SetupCheck() {
  const [d, setD] = useState<Doctor | null>(null);
  const [err, setErr] = useState("");
  const load = () => { setErr(""); api<Doctor>("/api/doctor").then(setD).catch((e) => setErr((e as Error).message)); };
  useEffect(load, []);
  return (
    <section className="card" aria-labelledby="h-doc">
      <h3 id="h-doc">Check my setup</h3>
      <div className="cb">
        {err && <div className="empty">{err}</div>}
        {!d && !err && <div className="empty">Checking…</div>}
        {d && (
          <>
            <p style={{ margin: 0 }}>
              {d.ready ? <b>Everything Busscript needs is installed.</b> : <b style={{ color: "var(--color-danger)" }}>Something is missing: {d.problems.join("; ")}.</b>}{" "}
              <span className="muted">{d.tip}</span>
            </p>
            <table className="tbl">
              <caption className="muted" style={{ textAlign: "left" }}>Libraries (installed for you by start.bat)</caption>
              <thead><tr><th scope="col">Part</th><th scope="col">What it does</th><th scope="col">Status</th></tr></thead>
              <tbody>
                {d.libraries.map((l) => (
                  <tr key={l.name}><td>{l.name}</td><td className="wrap">{l.why}</td>
                    <td>{l.ok ? `Installed ${l.version}` : <span style={{ color: "var(--color-danger)" }}>Missing. {l.fix}</span>}</td></tr>
                ))}
              </tbody>
            </table>
            <table className="tbl">
              <caption className="muted" style={{ textAlign: "left" }}>Adapter drivers (only needed for the adapter you own)</caption>
              <thead><tr><th scope="col">Adapter</th><th scope="col">Driver</th></tr></thead>
              <tbody>
                {d.drivers.map((x) => (
                  <tr key={x.interface}><td>{x.name}</td><td className="wrap">{x.found ? "Found" : <span className="muted">Not installed. {x.fix}</span>}</td></tr>
                ))}
                <tr><td>Serial adapters (SLCAN, CANable, Arduino)</td>
                  <td className="wrap">{d.serial_ports.length ? d.serial_ports.map((p) => `${p.port} (${p.label})`).join(", ") : <span className="muted">No serial port plugged in right now. No driver needed from us.</span>}</td></tr>
              </tbody>
            </table>
            <div><button className="btn" onClick={load}><RefreshCw size={13} /> Check again</button></div>
          </>
        )}
      </div>
    </section>
  );
}

/** One line saying what a frame means (J1939, OBD-II, UDS), or nothing when it is not one of those. */
export function Meaning({ id, ext, data }: { id: number; ext: boolean; data: string }) {
  const [text, setText] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    api<{ text: string | null }>(`/api/describe?id=${id}&ext=${ext}&data=${data}`).then((r) => live && setText(r.text)).catch(() => live && setText(null));
    return () => { live = false; };
  }, [id, ext, data]);
  return text ? <div style={{ marginBottom: 4 }}>Meaning: <b>{text}</b></div> : null;
}

/** Whole diagnostic conversations (fault codes, live data requests) pulled from the open file or the live buffer. */
export function DiagnosticsPage({ rev }: { rev: number }) {
  const [res, setRes] = useState<{ source: string; messages: DiagMsg[] } | null>(null);
  const load = () => { api<{ source: string; messages: DiagMsg[] }>("/api/diagnostics").then(setRes).catch(() => setRes({ source: "live", messages: [] })); };
  useEffect(load, [rev]);
  const tools = <button className="tb" onClick={load}><RefreshCw size={14} /><span>Refresh</span></button>;
  return (
    <div className="setup">
      <Pane title="Diagnostics" tools={tools}>
        <p className="muted" style={{ margin: "0 0 8px" }}>
          Car diagnostics (OBD-II and UDS) and truck messages (J1939) in plain words. This lists the diagnostic conversations
          found in {res?.source === "file" ? "the open file" : "what is on the bus right now"}. Fault codes such as P0133 appear here with their status.
        </p>
        {!res || res.messages.length === 0 ? (
          <div className="empty">No diagnostic messages yet. Start a measurement on a car or open a recording that contains diagnostic traffic, then press Refresh.</div>
        ) : (
          <table className="tbl">
            <thead><tr><th scope="col">Time</th><th scope="col">ID</th><th scope="col">Meaning</th></tr></thead>
            <tbody>
              {res.messages.map((m, i) => (
                <tr key={i}>
                  <td className="mono">{m.ts.toFixed(3)}</td>
                  <td className="mono">0x{m.id.toString(16).toUpperCase()}</td>
                  <td className="wrap">{m.text}{m.details.map((x) => <div key={x} className="mono">{x}</div>)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Pane>
    </div>
  );
}
