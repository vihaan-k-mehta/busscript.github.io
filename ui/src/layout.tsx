import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode, type RefObject } from "react";
import { Eye, RotateCcw } from "lucide-react";
import type { HeaderDrag } from "./dock";

/** Pane sizes as fractions. topH: height of the top row. topSplit: Trace share of the top row.
 *  botSplit: share of the bottom row taken by the Data/Write column. leftSplit: Data share of that column. */
export interface Sizes { topH: number; topSplit: number; botSplit: number; leftSplit: number }

export const DEFAULTS: Sizes = { topH: 0.42, topSplit: 0.66, botSplit: 0.42, leftSplit: 0.55 };
const KEY = "busscript-layout-v1";
const KEY2 = "busscript-panes-v1";

/** The five panes of the Measurement page. Dragging a pane onto another swaps their places. */
export type PaneId = "trace" | "stat" | "data" | "write" | "graphic";
export const PANE_IDS: PaneId[] = ["trace", "stat", "data", "write", "graphic"];
export const PANE_NAMES: Record<PaneId, string> = { trace: "Trace", stat: "Bus statistic", data: "Data", write: "Write", graphic: "Graphic" };
export interface Arrangement { order: PaneId[]; hidden: PaneId[] }

function loadArrangement(): Arrangement {
  try {
    const p = JSON.parse(localStorage.getItem(KEY2) ?? "null");
    const order = Array.isArray(p?.order) ? (p.order as PaneId[]) : [];
    const valid = order.length === PANE_IDS.length && PANE_IDS.every((id) => order.includes(id));
    const hidden = Array.isArray(p?.hidden) ? (p.hidden as PaneId[]).filter((id) => PANE_IDS.includes(id)) : [];
    return { order: valid ? order : [...PANE_IDS], hidden };
  } catch {
    return { order: [...PANE_IDS], hidden: [] };
  }
}

/** What a pane's header needs to offer: hide, drag to swap, move with the keyboard. */
export interface PaneControls {
  hide: (id: PaneId) => void;
  swap: (a: PaneId, b: PaneId) => void;
  nudge: (id: PaneId, delta: number) => void;
  drag?: HeaderDrag;                                         // dragging the title like a browser tab
  popOut?: (id: PaneId, screenX: number, screenY: number) => void;
  dockBack?: (id: PaneId) => void;
  inOwnWindow?: boolean;                                     // true inside a pop-out window
}
export const PaneContext = createContext<PaneControls | null>(null);
export const usePaneControls = () => useContext(PaneContext);
const MIN = 0.15;
const MAX = 0.85;

const clamp = (v: number) => Math.min(MAX, Math.max(MIN, v));

function load(): Sizes {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return DEFAULTS;
    const p = JSON.parse(raw);
    const out = { ...DEFAULTS };
    for (const k of Object.keys(DEFAULTS) as (keyof Sizes)[]) if (typeof p[k] === "number") out[k] = clamp(p[k]);
    return out;
  } catch {
    return DEFAULTS;
  }
}

export function useLayout() {
  const [sizes, setSizes] = useState<Sizes>(load);
  const save = (s: Sizes) => { try { localStorage.setItem(KEY, JSON.stringify(s)); } catch { /* private window: layout just will not persist */ } };
  const set = useCallback((key: keyof Sizes, value: number) => {
    setSizes((s) => { const n = { ...s, [key]: clamp(value) }; save(n); return n; });
  }, []);
  const [arr, setArr] = useState<Arrangement>(loadArrangement);
  const saveArr = (a: Arrangement) => { try { localStorage.setItem(KEY2, JSON.stringify(a)); } catch { /* not remembered */ } };
  const update = useCallback((fn: (a: Arrangement) => Arrangement) => setArr((a) => { const n = fn(a); saveArr(n); return n; }), []);
  const exchange = (a: Arrangement, i: number, j: number): Arrangement => {
    if (i === j || j < 0 || j >= a.order.length) return a;
    const order = [...a.order];
    [order[i], order[j]] = [order[j], order[i]];
    return { ...a, order };
  };
  const controls: PaneControls = {
    hide: (id) => update((a) => ({ ...a, hidden: a.hidden.includes(id) ? a.hidden : [...a.hidden, id] })),
    swap: (x, y) => update((a) => exchange(a, a.order.indexOf(x), a.order.indexOf(y))),
    nudge: (id, d) => update((a) => exchange(a, a.order.indexOf(id), a.order.indexOf(id) + d)),
  };
  const setVisible = (id: PaneId, show: boolean) =>
    update((a) => ({ ...a, hidden: show ? a.hidden.filter((h) => h !== id) : a.hidden.includes(id) ? a.hidden : [...a.hidden, id] }));
  const resetSizes = useCallback(() => { setSizes(DEFAULTS); save(DEFAULTS); }, []);
  const resetAll = useCallback(() => { setSizes(DEFAULTS); save(DEFAULTS); update(() => ({ order: [...PANE_IDS], hidden: [] })); }, [update]);
  const sameSizes = (Object.keys(DEFAULTS) as (keyof Sizes)[]).every((k) => Math.abs(sizes[k] - DEFAULTS[k]) < 0.001);
  const sameArr = arr.hidden.length === 0 && arr.order.every((id, i) => id === PANE_IDS[i]);
  return { sizes, set, resetSizes, reset: resetAll, isDefault: sameSizes && sameArr, order: arr.order, hidden: arr.hidden, controls, setVisible };
}

/** A draggable, keyboard-operable divider. "vertical" = a vertical bar that changes widths. */
export function Splitter({ orientation, container, value, onChange, onReset, label }: {
  orientation: "vertical" | "horizontal";
  container: RefObject<HTMLElement>;
  value: number;
  onChange: (v: number) => void;
  onReset: () => void;
  label: string;
}) {
  const [drag, setDrag] = useState(false);
  const active = useRef(false);

  const move = (clientX: number, clientY: number) => {
    const el = container.current;
    if (!el || !active.current) return;
    const r = el.getBoundingClientRect();
    onChange(orientation === "vertical" ? (clientX - r.left) / r.width : (clientY - r.top) / r.height);
  };

  return (
    <div
      className={`split ${orientation} ${drag ? "drag" : ""}`}
      role="separator"
      aria-orientation={orientation}
      aria-label={label}
      aria-valuenow={Math.round(value * 100)}
      aria-valuemin={Math.round(MIN * 100)}
      aria-valuemax={Math.round(MAX * 100)}
      tabIndex={0}
      title="Drag to resize. Arrow keys also work. Double-click to reset."
      onPointerDown={(e) => { (e.target as HTMLElement).setPointerCapture(e.pointerId); active.current = true; setDrag(true); e.preventDefault(); }}
      onPointerMove={(e) => move(e.clientX, e.clientY)}
      onPointerUp={() => { active.current = false; setDrag(false); }}
      onPointerCancel={() => { active.current = false; setDrag(false); }}
      onDoubleClick={onReset}
      onKeyDown={(e) => {
        const step = e.shiftKey ? 0.1 : 0.02;
        const dec = orientation === "vertical" ? "ArrowLeft" : "ArrowUp";
        const inc = orientation === "vertical" ? "ArrowRight" : "ArrowDown";
        if (e.key === dec) { onChange(value - step); e.preventDefault(); }
        else if (e.key === inc) { onChange(value + step); e.preventDefault(); }
        else if (e.key === "Home") { onChange(MIN); e.preventDefault(); }
        else if (e.key === "End") { onChange(MAX); e.preventDefault(); }
        else if (e.key === "Enter") { onReset(); e.preventDefault(); }
      }}
    />
  );
}

export const Cell = ({ grow, children }: { grow: number; children: ReactNode }) => (
  <div className="lcell" style={{ flex: `${grow} 1 0` }}>{children}</div>
);

/** The View menu: tick the panes you want to see. */
export function ViewMenu({ hidden, popped = [], onToggle, onDock, onReset, canReset }: { hidden: PaneId[]; popped?: PaneId[]; onToggle: (id: PaneId, show: boolean) => void; onDock?: (id: PaneId) => void; onReset: () => void; canReset: boolean }) {
  const [open, setOpen] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => { if (box.current && !box.current.contains(e.target as Node)) setOpen(false); };
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    document.addEventListener("mousedown", away);
    document.addEventListener("keydown", esc);
    return () => { document.removeEventListener("mousedown", away); document.removeEventListener("keydown", esc); };
  }, [open]);
  return (
    <div className="viewmenu" ref={box}>
      <button className="tb" aria-haspopup="true" aria-expanded={open} onClick={() => setOpen(!open)} title="Choose which panes to show">
        <Eye size={14} /><span>View</span>
      </button>
      {open && (
        <div className="menu" role="group" aria-label="Show panes">
          {PANE_IDS.map((id) => (
            popped.includes(id)
              ? <div key={id} className="away">{PANE_NAMES[id]} <span className="muted">(in its own window)</span> <button className="btn" onClick={() => onDock?.(id)}>Bring back</button></div>
              : <label key={id}><input type="checkbox" checked={!hidden.includes(id)} onChange={(e) => onToggle(id, e.target.checked)} /> {PANE_NAMES[id]}</label>
          ))}
          <hr />
          <button className="btn" onClick={() => { onReset(); setOpen(false); }} disabled={!canReset}><RotateCcw size={13} /> Put everything back</button>
          <span className="muted">Drag a pane by its title to swap it with another. Drag it out of the window to pop it out.</span>
        </div>
      )}
    </div>
  );
}
