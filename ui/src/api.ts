// Token handling: the server prints a URL with ?token=...; we keep it for later visits.
const KEY = "busscript-token";

function readToken(): string {
  const fromUrl = new URLSearchParams(location.search).get("token");
  try {
    if (fromUrl) {
      localStorage.setItem(KEY, fromUrl);
      history.replaceState(null, "", location.pathname + location.hash);
      return fromUrl;
    }
    return localStorage.getItem(KEY) ?? "";
  } catch {
    return fromUrl ?? "";
  }
}

export const token = readToken();

export class ApiError extends Error {}

export async function api<T = unknown>(path: string, method = "GET", body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method,
    headers: { Authorization: `Bearer ${token}`, ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail);
    } catch {
      /* keep statusText */
    }
    throw new ApiError(res.status === 401 ? "Not authorised: open the link printed by the server (it contains the token)." : msg);
  }
  return (await res.json()) as T;
}

export interface Channel {
  name: string; interface: string; channel: string; bitrate: number;
  fd_enabled: boolean; fd_data_bitrate: number | null; listen_only: boolean;
  open: boolean; databases: string[];
}
export interface Frame { ts: number; ch: string; id: number; ext: boolean; fd: boolean; dir: "rx" | "tx"; dlc: number; data: string; error: boolean; name: string | null }
export interface Stats { rx_frames: number; tx_frames: number; error_frames: number; rx_bytes: number; tx_bytes: number; load_pct: number; peak_load_pct: number; rx_rate: number; tx_rate: number }
export interface BusState { demo?: boolean; running: boolean; elapsed: number; channels: number; frames_buffered: number; logging: null | { path: string; format: string; frames: number }; replay: null | { path: string; position: number }; cyclic: { id: string; channel: string; can_id: number; data: string; period_ms: number }[] }
export interface SignalValue { channel: string; message: string; signal: string; value: number | string; unit: string; raw: number | null; ts: number; min?: number | null; max?: number | null }
export interface CatalogueMsg { channel: string; id: number; ext: boolean; name: string; dlc: number; signals: { name: string; unit: string; min: number | null; max: number | null }[] }
export interface Filter { id: string; mode: "pass" | "stop"; id_from: number; id_to: number; channel: string | null; ext: boolean; direction: "rx" | "tx" | "both"; enabled: boolean }

export interface FileSummary { name: string; format: string; frames: number; truncated: boolean; duration: number; channels: string[]; ids: number; error_frames: number }
export interface StoredFile { name: string; kind: "log" | "database"; size: number; modified: number }
export interface OverviewRow { id: number; ext: boolean; name: string | null; count: number; first: number; last: number; rate: number | null }
export type OpenResult =
  | ({ kind: "log" } & FileSummary)
  | { kind: "database"; channel: string; created_channel: boolean; path: string; messages: number; signals: number };

/** Upload a file the user picked or dropped. The server stores it in its uploads folder. */
export function uploadFile(file: File, onProgress: (pct: number) => void): Promise<{ name: string; size: number; kind: string }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", `/api/files/${encodeURIComponent(file.name)}`);
    xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.upload.onprogress = (e) => { if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100)); };
    xhr.onerror = () => reject(new ApiError("Could not send the file. Is Busscript still running?"));
    xhr.onload = () => {
      let body: { detail?: unknown; name?: string; size?: number; kind?: string } = {};
      try { body = JSON.parse(xhr.responseText); } catch { /* not JSON */ }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as { name: string; size: number; kind: string });
      else reject(new ApiError(xhr.status === 401 ? "Not authorised: open the link printed by the server (it contains the token)." : typeof body.detail === "string" ? body.detail : "The file could not be sent."));
    };
    xhr.send(file);
  });
}
