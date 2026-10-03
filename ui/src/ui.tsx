import { useEffect, useRef, useState, type ReactNode } from "react";
import { Maximize2, Minimize2 } from "lucide-react";

export function Pane({ title, icon, children, tools, className = "" }: { title: string; icon?: ReactNode; children: ReactNode; tools?: ReactNode; className?: string }) {
  const [max, setMax] = useState(false);
  useEffect(() => {
    if (!max) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMax(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [max]);
  const id = `pane-${title.replace(/\W+/g, "-").toLowerCase()}`;
  return (
    <section className={`pane ${max ? "max" : ""} ${className}`} aria-labelledby={id}>
      <header>
        {icon}
        <h2 id={id}>{title}</h2>
        <button className="tb" onClick={() => setMax(!max)} aria-label={max ? `Restore ${title}` : `Maximise ${title}`}>
          {max ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
        </button>
      </header>
      {tools && <div className="tools">{tools}</div>}
      <div className="body">{children}</div>
    </section>
  );
}

export function Modal({ title, open, onClose, children, footer }: { title: string; open: boolean; onClose: () => void; children: ReactNode; footer?: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog ref={ref} onClose={onClose} onCancel={onClose} aria-labelledby="dlg-title">
      <div className="dh" id="dlg-title">{title}</div>
      <div className="db">{open && children}</div>
      <div className="df">{footer ?? <button className="btn" onClick={onClose}>Close</button>}</div>
    </dialog>
  );
}

export function Field({ label, children, error }: { label: string; children: ReactNode; error?: string }) {
  return (
    <label className="f">
      <span>{label}</span>
      {children}
      {error && <span className="err-msg" role="alert">{error}</span>}
    </label>
  );
}

export function Toast({ msg, kind, onDone }: { msg: string; kind: "info" | "error"; onDone: () => void }) {
  useEffect(() => {
    const t = window.setTimeout(onDone, kind === "error" ? 7000 : 3000);
    return () => window.clearTimeout(t);
  }, [msg, kind, onDone]);
  return <div className={`toast ${kind}`} role={kind === "error" ? "alert" : "status"}>{msg}</div>;
}

export type Notify = (msg: string, kind?: "info" | "error") => void;
