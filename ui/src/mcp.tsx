import { useCallback, useEffect, useState } from "react";
import { api, token } from "./api";
import { Modal, type Notify } from "./ui";

interface Activity { ts: number; tool: string; args: string; ok: boolean; detail: string }
type Settings = Record<string, string | string[]>;

export function McpDialog({ open, onClose, notify }: { open: boolean; onClose: () => void; notify: Notify }) {
  const [s, setS] = useState<Settings>({});
  const [act, setAct] = useState<Activity[]>([]);
  const [showToken, setShowToken] = useState(false);
  const load = useCallback(() => {
    api<Settings>("/api/settings").then(setS).catch((e) => notify((e as Error).message, "error"));
    api<Activity[]>("/api/mcp/activity").then(setAct).catch(() => {});
  }, [notify]);
  useEffect(() => {
    if (!open) return;
    load();
    const t = window.setInterval(load, 2000);
    return () => window.clearInterval(t);
  }, [open, load]);

  const put = async (key: string, value: unknown) => {
    try { await api(`/api/settings/${key}`, "PUT", { value }); load(); } catch (e) { notify((e as Error).message, "error"); }
  };
  const on = s["mcp.enabled"] === "true";
  const tx = s["mcp.allow_transmit"] === "true";
  const disabled: string[] = (() => { try { return JSON.parse(String(s["mcp.tools_disabled"] ?? "[]")); } catch { return []; } })();
  const tools = (s["mcp.tools_all"] as string[]) ?? [];
  const url = `${location.origin}/mcp`;
  const cfg = JSON.stringify({ mcpServers: { "busscript": { type: "http", url, headers: { Authorization: `Bearer ${token}` } } } }, null, 2);

  return (
    <Modal title="MCP server" open={open} onClose={onClose}>
      <label>
        <input type="checkbox" checked={on} onChange={(e) => put("mcp.enabled", e.target.checked ? "true" : "false")} /> <b>Enable MCP</b>{" "}
        <span className="muted">(AI clients can call the tools below while this is on)</span>
      </label>
      <div><span className={`dot ${on ? "ok" : ""}`} />{on ? "On" : "Off"}. Endpoint {url} (this computer only, token required)</div>
      <div>
        <b>Connect a client</b>
        <pre className="mono" style={{ whiteSpace: "pre-wrap", wordBreak: "break-all", background: "var(--color-surface)", padding: 8, margin: "4px 0" }}>
          {showToken ? cfg : cfg.split(token).join("********")}
        </pre>
        <button className="btn" onClick={() => setShowToken(!showToken)}>{showToken ? "Hide token" : "Show token"}</button>{" "}
        <button className="btn" onClick={() => navigator.clipboard.writeText(cfg).then(() => notify("Copied (includes the token)"), () => notify("Copy failed", "error"))}>Copy config</button>
      </div>
      <div>
        <b>Tools</b>
        {tools.map((t) => {
          const isTx = t === "send_frame" || t === "send_signal";
          return (
            <div key={t}>
              <label>
                <input type="checkbox" disabled={isTx && !tx} checked={!disabled.includes(t) && (!isTx || tx)}
                  onChange={(e) => put("mcp.tools_disabled", e.target.checked ? disabled.filter((d) => d !== t) : [...disabled, t])} />{" "}
                <span className="mono">{t}</span>
              </label>
            </div>
          );
        })}
      </div>
      <label>
        <input type="checkbox" checked={tx} onChange={(e) => put("mcp.allow_transmit", e.target.checked ? "true" : "false")} /> <b>Allow MCP to transmit frames</b>
        <div className="muted">Off by default. Even when on, channels set to listen-only still refuse to send. Sending on a real vehicle bus can affect real equipment.</div>
      </label>
      <div>
        <b>Activity</b>
        {act.length === 0 ? <div className="empty">No calls yet.</div> : (
          <table className="tbl">
            <thead><tr><th scope="col">Time</th><th scope="col">Tool</th><th scope="col">Result</th></tr></thead>
            <tbody>
              {act.slice(0, 50).map((a, i) => (
                <tr key={i}>
                  <td className="mono">{new Date(a.ts * 1000).toLocaleTimeString()}</td>
                  <td className="mono">{a.tool}</td>
                  <td style={{ color: a.ok ? undefined : "var(--color-danger)" }}>{a.ok ? "ok" : `refused: ${a.detail}`}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </Modal>
  );
}
