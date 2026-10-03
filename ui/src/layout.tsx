import { useCallback, useRef, useState, type ReactNode, type RefObject } from "react";

/** Pane sizes as fractions. topH: height of the top row. topSplit: Trace share of the top row.
 *  botSplit: share of the bottom row taken by the Data/Write column. leftSplit: Data share of that column. */
export interface Sizes { topH: number; topSplit: number; botSplit: number; leftSplit: number }

export const DEFAULTS: Sizes = { topH: 0.42, topSplit: 0.66, botSplit: 0.42, leftSplit: 0.55 };
const KEY = "busscript-layout-v1";
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
  const reset = useCallback(() => { setSizes(DEFAULTS); save(DEFAULTS); }, []);
  const isDefault = (Object.keys(DEFAULTS) as (keyof Sizes)[]).every((k) => Math.abs(sizes[k] - DEFAULTS[k]) < 0.001);
  return { sizes, set, reset, isDefault };
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
