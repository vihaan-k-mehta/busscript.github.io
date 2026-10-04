import { useCallback, useEffect, useRef, useState, type PointerEvent as RPointerEvent } from "react";
import { PANE_IDS, PANE_NAMES, type PaneId } from "./layout";

/* Moving panes around: drag a pane's title like a browser tab. Drop it on another pane to swap places, or let go
   outside the window (or press the pop-out button) to open it in a window of its own. Windows talk to each other
   over a BroadcastChannel, so the main window knows which panes are away and brings them back when they close. */

const CHANNEL = "busscript-panes";
const GRACE = 4000;                      // a pop-out window must say hello within this long, or its pane returns
type Msg = { type: "alive" | "closed" | "who" | "dock"; pane?: PaneId };

const hasChannel = typeof BroadcastChannel !== "undefined";
export const lineChannel = hasChannel ? new BroadcastChannel("busscript-lines") : null;

interface PywebviewApi { pop_out?: (pane: string, x: number, y: number, w: number, h: number) => void; close_popout?: (pane: string) => void }
const nativeApi = (): PywebviewApi | undefined => (window as unknown as { pywebview?: { api?: PywebviewApi } }).pywebview?.api;

/** Open a pane in its own window: a real native window inside the Busscript program, else a browser pop-up. */
export function openPopOut(pane: PaneId, sx: number, sy: number) {
  const w = 780, h = 520;
  const x = Math.max(0, Math.round(sx)), y = Math.max(0, Math.round(sy));
  const api = nativeApi();
  if (api?.pop_out) { api.pop_out(pane, x, y, w, h); return; }
  window.open(`${location.pathname}?pane=${pane}`, `busscript-${pane}`, `popup,width=${w},height=${h},left=${x},top=${y}`);
}

export function closePopOut(pane: PaneId) {
  const api = nativeApi();
  if (api?.close_popout) api.close_popout(pane); else window.close();
}

/** Which panes are in windows of their own right now (main window side). */
export function usePopped() {
  const [seen, setSeen] = useState<Partial<Record<PaneId, number>>>({});
  const chan = useRef<BroadcastChannel | null>(null);
  useEffect(() => {
    if (!hasChannel) return;
    const c = new BroadcastChannel(CHANNEL);
    chan.current = c;
    c.onmessage = (e) => {
      const m = e.data as Msg;
      if (!m.pane) return;
      if (m.type === "alive") setSeen((s) => ({ ...s, [m.pane!]: Date.now() }));
      if (m.type === "closed") setSeen((s) => { const n = { ...s }; delete n[m.pane!]; return n; });
    };
    c.postMessage({ type: "who" } as Msg);     // after a reload: windows that are still open answer at once
    const t = window.setInterval(() => setSeen((s) => {
      const now = Date.now();
      const n: typeof s = {};
      let changed = false;
      for (const k of Object.keys(s) as PaneId[]) { if (now - (s[k] as number) < GRACE) n[k] = s[k]; else changed = true; }
      return changed ? n : s;
    }), 1000);
    return () => { window.clearInterval(t); c.close(); chan.current = null; };
  }, []);
  const popOut = useCallback((id: PaneId, sx: number, sy: number) => {
    setSeen((s) => ({ ...s, [id]: Date.now() + GRACE }));      // hide it here straight away
    openPopOut(id, sx, sy);
  }, []);
  const dockBack = useCallback((id: PaneId) => {
    chan.current?.postMessage({ type: "dock", pane: id } as Msg);
    setSeen((s) => { const n = { ...s }; delete n[id]; return n; });
  }, []);
  return { popped: PANE_IDS.filter((id) => seen[id] !== undefined), popOut, dockBack };
}

/** Pop-out window side: say hello, and close when asked to dock back. */
export function usePopOutPresence(pane: PaneId) {
  useEffect(() => {
    if (!hasChannel) return;
    const c = new BroadcastChannel(CHANNEL);
    const hello = () => c.postMessage({ type: "alive", pane } as Msg);
    hello();
    const t = window.setInterval(hello, 1000);
    c.onmessage = (e) => {
      const m = e.data as Msg;
      if (m.type === "who") hello();
      if (m.type === "dock" && m.pane === pane) closePopOut(pane);
    };
    const bye = () => c.postMessage({ type: "closed", pane } as Msg);
    window.addEventListener("pagehide", bye);
    return () => { window.clearInterval(t); window.removeEventListener("pagehide", bye); bye(); c.close(); };
  }, [pane]);
}

/** A value kept in localStorage and shared live between all Busscript windows. */
export function useShared<T>(key: string, initial: T): [T, (v: T | ((p: T) => T)) => void] {
  const read = (): T => { try { const r = localStorage.getItem(key); return r === null ? initial : (JSON.parse(r) as T); } catch { return initial; } };
  const [value, setValue] = useState<T>(read);
  useEffect(() => {
    const on = (e: StorageEvent) => { if (e.key === key) setValue(read()); };
    window.addEventListener("storage", on);
    return () => window.removeEventListener("storage", on);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const set = useCallback((v: T | ((p: T) => T)) => {
    setValue((p) => {
      const n = typeof v === "function" ? (v as (p: T) => T)(p) : v;
      try { localStorage.setItem(key, JSON.stringify(n)); } catch { /* not shared this time */ }
      return n;
    });
  }, [key]);
  return [value, set];
}

/* ------------------------------------------------------------------ dragging a pane by its title */
export interface DragState { id: PaneId; x: number; y: number; over: PaneId | null; outside: boolean }
export interface HeaderDrag {
  state: DragState | null;
  down: (id: PaneId, e: RPointerEvent<HTMLElement>) => void;
  move: (e: RPointerEvent<HTMLElement>) => void;
  up: (e: RPointerEvent<HTMLElement>) => void;
  cancel: () => void;
}

export function useHeaderDrag(onSwap: (a: PaneId, b: PaneId) => void, onPopOut: (id: PaneId, sx: number, sy: number) => void): HeaderDrag {
  const [state, setState] = useState<DragState | null>(null);
  const start = useRef<{ id: PaneId; x: number; y: number } | null>(null);
  const live = useRef<DragState | null>(null);
  const set = (s: DragState | null) => { live.current = s; setState(s); };

  const probe = (e: RPointerEvent<HTMLElement>, id: PaneId): DragState => {
    const outside = e.clientX < 0 || e.clientY < 0 || e.clientX > window.innerWidth || e.clientY > window.innerHeight;
    const el = outside ? null : document.elementFromPoint(e.clientX, e.clientY);
    const target = (el?.closest("[data-pane]") as HTMLElement | null)?.dataset.pane as PaneId | undefined;
    return { id, x: e.clientX, y: e.clientY, outside, over: target && target !== id ? target : null };
  };
  const down = (id: PaneId, e: RPointerEvent<HTMLElement>) => {
    if (e.button !== 0 || (e.target as HTMLElement).closest("button, input, select, a, textarea")) return;
    start.current = { id, x: e.clientX, y: e.clientY };
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* the drag still works inside the window */ }
  };
  const move = (e: RPointerEvent<HTMLElement>) => {
    const s = start.current;
    if (!s) return;
    if (!live.current && Math.hypot(e.clientX - s.x, e.clientY - s.y) < 5) return;
    set(probe(e, s.id));
  };
  const up = (e: RPointerEvent<HTMLElement>) => {
    const s = start.current;
    start.current = null;
    try { e.currentTarget.releasePointerCapture(e.pointerId); } catch { /* already released */ }
    if (!s || !live.current) { set(null); return; }
    const end = probe(e, s.id);
    set(null);
    if (end.outside) onPopOut(s.id, e.screenX, e.screenY);
    else if (end.over) onSwap(s.id, end.over);
  };
  const cancel = () => { start.current = null; set(null); };
  useEffect(() => {
    if (!state) return;
    const esc = (e: KeyboardEvent) => { if (e.key === "Escape") cancel(); };
    window.addEventListener("keydown", esc);
    return () => window.removeEventListener("keydown", esc);
  }, [state === null]);          // eslint-disable-line react-hooks/exhaustive-deps
  return { state, down, move, up, cancel };
}

/** The little "tab" that follows the pointer while a pane is being dragged. */
export function DragGhost({ drag }: { drag: DragState | null }) {
  if (!drag) return null;
  return (
    <div className="ghost" style={{ left: drag.x + 14, top: drag.y + 10 }} aria-hidden="true">
      <b>{PANE_NAMES[drag.id]}</b>
      <span>{drag.over ? `swap with ${PANE_NAMES[drag.over]}` : "drop on a pane to swap · drag out of the window to pop out"}</span>
    </div>
  );
}
