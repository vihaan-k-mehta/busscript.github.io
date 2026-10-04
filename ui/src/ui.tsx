import { useEffect, useRef, useState, type ReactNode } from "react";
import { ExternalLink, GripVertical, Maximize2, Minimize2, PanelTopClose, X } from "lucide-react";
import { usePaneControls, type PaneId } from "./layout";

export function Pane({ title, icon, children, tools, className = "", paneId }: { title: string; icon?: ReactNode; children: ReactNode; tools?: ReactNode; className?: string; paneId?: PaneId }) {
  const [max, setMax] = useState(false);
  const ctl = usePaneControls();
  const own = !!ctl?.inOwnWindow;                       // this pane is alone in a pop-out window
  const movable = !!(paneId && ctl && !own);
  const drag = ctl?.drag?.state ?? null;
  useEffect(() => {
    if (!max) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setMax(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [max]);
  const id = `pane-${title.replace(/\W+/g, "-").toLowerCase()}`;
  const d = ctl?.drag;
  return (
    <section className={`pane ${max ? "max" : ""} ${drag && drag.over === paneId ? "swap-target" : ""} ${drag && drag.id === paneId ? "lifted" : ""} ${className}`} aria-labelledby={id} data-pane={paneId}>
      <header className={movable && !max ? "movable" : ""} title={movable ? "Drag this title onto another pane to swap places, or out of the window to pop it out" : undefined}
        onPointerDown={movable && !max && d && paneId ? (e) => d.down(paneId, e) : undefined}
        onPointerMove={movable && d ? d.move : undefined}
        onPointerUp={movable && d ? d.up : undefined}
        onPointerCancel={movable && d ? d.cancel : undefined}>
        {movable && (
          <button className="tb grip" aria-label={`Move ${title}. Arrow keys swap it with the next pane.`}
            onKeyDown={(e) => {
              const dir = e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : 0;
              if (dir) { e.preventDefault(); ctl!.nudge(paneId!, dir); }
            }}><GripVertical size={14} /></button>
        )}
        {icon}
        <h2 id={id}>{title}</h2>
        {movable && ctl?.popOut && (
          <button className="tb" onClick={() => ctl.popOut!(paneId!, window.screenX + 80, window.screenY + 80)} aria-label={`Pop out ${title} into its own window`} title="Open this pane in a window of its own">
            <ExternalLink size={14} />
          </button>
        )}
        {own && ctl?.dockBack && (
          <button className="tb" onClick={() => ctl.dockBack!(paneId!)} aria-label={`Put ${title} back in the main window`} title="Put this pane back in the main window"><PanelTopClose size={14} /></button>
        )}
        {!own && (
          <button className="tb" onClick={() => setMax(!max)} aria-label={max ? `Restore ${title}` : `Maximise ${title}`}>
            {max ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
          </button>
        )}
        {movable && <button className="tb" onClick={() => ctl!.hide(paneId!)} aria-label={`Hide ${title}`} title="Hide this pane (bring it back from View)"><X size={14} /></button>}
      </header>
      {tools && <div className="tools">{tools}</div>}
      <div className="body" tabIndex={0}>{children}</div>
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
  // keep the latest callback in a ref: the parent re-renders many times a second while data streams in, and a
  // callback in the effect's dependencies would restart the timer every time, so the message would never leave
  const done = useRef(onDone);
  done.current = onDone;
  useEffect(() => {
    const t = window.setTimeout(() => done.current(), kind === "error" ? 7000 : 3000);
    return () => window.clearTimeout(t);
  }, [msg, kind]);
  return <div className={`toast ${kind}`} role={kind === "error" ? "alert" : "status"}>{msg}</div>;
}

export type Notify = (msg: string, kind?: "info" | "error") => void;
