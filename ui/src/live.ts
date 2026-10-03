import { useSyncExternalStore } from "react";
import { token, type BusState, type Frame, type Stats } from "./api";

const MAX_ROWS = 50_000;

/** One shared live connection. Frames go in a plain array; React re-renders at most every 100 ms. */
class Live {
  frames: Frame[] = [];
  stats: Record<string, Stats> = {};
  state: BusState | null = null;
  dropped = 0;
  connected = false;
  version = 0;
  private listeners = new Set<() => void>();
  private ws: WebSocket | null = null;
  private timer: number | null = null;
  private retry = 0;

  start() {
    if (this.ws) return;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${proto}://${location.host}/ws?token=${encodeURIComponent(token)}`);
    this.ws = ws;
    ws.onopen = () => { this.connected = true; this.retry = 0; this.bump(); };
    ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.type === "frames") {
        for (const f of m.frames as Frame[]) this.frames.push(f);
        if (this.frames.length > MAX_ROWS) this.frames.splice(0, this.frames.length - MAX_ROWS);
        this.dropped = m.dropped;
      } else if (m.type === "tick") {
        this.state = m.state;
        this.stats = m.stats;
      }
      this.bump();
    };
    ws.onclose = () => {
      this.ws = null; this.connected = false; this.bump();
      window.setTimeout(() => this.start(), Math.min(5000, 500 * 2 ** this.retry++));
    };
  }

  clear() { this.frames = []; this.bump(); }

  private bump() {
    if (this.timer !== null) return;
    this.timer = window.setTimeout(() => {
      this.timer = null;
      this.version++;
      this.listeners.forEach((l) => l());
    }, 100);
  }

  subscribe = (l: () => void) => { this.listeners.add(l); return () => this.listeners.delete(l); };
  getVersion = () => this.version;
}

export const live = new Live();
export function useLive() {
  useSyncExternalStore(live.subscribe, live.getVersion);
  return live;
}
