import { useEffect, useState } from "react";
import { FileUp, Trash2 } from "lucide-react";
import { api, uploadFile, type OpenResult, type StoredFile } from "./api";
import { Modal, type Notify } from "./ui";

export interface Busy { text: string; pct?: number }

const ACCEPT = ".asc,.blf,.mf4,.mdf,.log,.trc,.csv,.db,.dbc,.kcd,.sym,.arxml,.cdd";
const isDb = (n: string) => /\.(dbc|kcd|sym|arxml|cdd)$/i.test(n);

const fmtSize = (b: number) => (b > 1048576 ? `${(b / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(b / 1024))} KB`);

/** Upload then open each file. Databases go first so the recording is named as soon as it opens. */
export async function importFiles(files: File[], cb: { busy: (b: Busy | null) => void; notify: Notify; opened: (r: OpenResult) => void }) {
  const ordered = [...files].sort((a, b) => Number(isDb(b.name)) - Number(isDb(a.name)));
  try {
    for (const f of ordered) {
      cb.busy({ text: `Sending ${f.name}…`, pct: 0 });
      const up = await uploadFile(f, (pct) => cb.busy({ text: `Sending ${f.name}…`, pct }));
      cb.busy({ text: isDb(f.name) ? `Reading ${up.name}…` : `Reading ${up.name}. Big recordings can take a little while…` });
      cb.opened(await api<OpenResult>("/api/files/open", "POST", { name: up.name }));
    }
  } catch (e) {
    cb.notify((e as Error).message, "error");
  } finally {
    cb.busy(null);
  }
}

export function describe(r: OpenResult): string {
  if (r.kind === "database")
    return `Attached the database to channel "${r.channel}": ${r.messages} messages, ${r.signals} signals.` + (r.created_channel ? " A channel was created to hold it." : "");
  return `Opened ${r.name}: ${r.frames.toLocaleString()} frames, ${r.duration} s.` + (r.truncated ? " It was too long to load completely, so only the first part is shown." : "");
}

export function BusyOverlay({ busy }: { busy: Busy | null }) {
  if (!busy) return null;
  return (
    <div className="busy" role="status" aria-live="polite">
      <div className="busy-box">
        <strong>{busy.text}</strong>
        {busy.pct !== undefined ? <progress max={100} value={busy.pct} aria-label="Upload progress" /> : <progress aria-label="Working" />}
      </div>
    </div>
  );
}

export function DropOverlay({ show }: { show: boolean }) {
  if (!show) return null;
  return <div className="drop" role="presentation"><div className="drop-box"><FileUp size={28} aria-hidden /><strong>Drop a file to open it</strong><span>Recordings and database files</span></div></div>;
}

export function OpenFileDialog({ open, onClose, onPick, onOpenStored, notify }: {
  open: boolean; onClose: () => void; onPick: (files: File[]) => void; onOpenStored: (name: string) => void; notify: Notify;
}) {
  const [stored, setStored] = useState<StoredFile[]>([]);
  const [formats, setFormats] = useState("");
  const load = () => api<{ stored: StoredFile[]; supported: string }>("/api/files").then((r) => { setStored(r.stored); setFormats(r.supported); }).catch(() => {});
  useEffect(() => { if (open) load(); }, [open]);
  const del = async (n: string) => {
    try { await api(`/api/files/stored/${encodeURIComponent(n)}`, "DELETE"); load(); } catch (e) { notify((e as Error).message, "error"); }
  };
  return (
    <Modal title="Open a file" open={open} onClose={onClose}>
      <p style={{ margin: 0 }}>Open a recording to look at it without any hardware, or a database file so signals get their names. You can also just <strong>drag a file onto the window</strong>.</p>
      <label className="btn primary filebtn">
        <FileUp size={14} aria-hidden /> Choose a file…
        <input type="file" accept={ACCEPT} multiple className="sr-only" aria-label="Choose a file to open"
          onChange={(e) => { const fs = [...(e.target.files ?? [])]; e.target.value = ""; if (fs.length) { onClose(); onPick(fs); } }} />
      </label>
      <p className="muted" style={{ margin: 0, fontSize: "var(--text-sm)" }}>
        Recordings: ASC, BLF, MF4 (MDF4), candump .log, PEAK .trc, CSV, SQLite .db. Databases: DBC, KCD, SYM, ARXML, CDD.
        {formats ? "" : ""} MF4 files must contain recorded CAN frames.
      </p>
      <div>
        <b>Files you opened before</b>
        {stored.length === 0 ? <div className="empty" style={{ padding: "8px 0" }}>None yet.</div> : (
          <table className="tbl">
            <thead><tr><th scope="col">Name</th><th scope="col">Size</th><th /></tr></thead>
            <tbody>
              {stored.map((f) => (
                <tr key={f.name}>
                  <td>{f.name}</td><td>{fmtSize(f.size)}</td>
                  <td style={{ textAlign: "right" }}>
                    <button className="btn" onClick={() => { onClose(); onOpenStored(f.name); }}>{f.kind === "database" ? "Attach" : "Open"}</button>{" "}
                    <button className="btn danger" aria-label={`Delete ${f.name}`} onClick={() => del(f.name)}><Trash2 size={13} /></button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Modal>
  );
}
