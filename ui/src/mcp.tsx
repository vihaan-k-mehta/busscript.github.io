import { useCallback, useEffect, useState } from "react";
import { api, token } from "./api";
import { Modal, type Notify } from "./ui";

interface Activity { ts: number; tool: string; args: string; ok: boolean; detail: string }
type Settings = Record<string, string | string[]>;

export function McpDialog({ open, onClose, notify }: { open: boolean; onClose: () => void; notify: Notify }) {
  const [s, setS] = useState<Settings>({});
  const [act, setAct] = useState<Activity[]>([]);
  const [showToken, setShowToken] = useState(false);
  const [qs, setQs] = useState<{ prompt: string; stdio: unknown } | null>(null);
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
  useEffect(() => { if (open) api<{ prompt: string; stdio: unknown }>("/api/mcp/quickstart").then(setQs).catch(() => {}); }, [open]);
  const copy = (text: string, done: string) => navigator.clipboard.writeText(text).then(() => notify(done), () => notify("Copy failed. Select the text and copy it by hand.", "error"));
  /** One click: switch MCP on, then copy the whole prompt. */
  const quick = async () => {
    if (!qs) return;
    if (!on) await put("mcp.enabled", "true");
    await copy(qs.prompt, "Copied. Now paste it into Claude Code.");
  };
  const stdioCfg = JSON.stringify(qs?.stdio ?? {}, null, 2);
  const box = { whiteSpace: "pre-wrap" as const, overflowWrap: "anywhere" as const, background: "var(--color-surface)", padding: 8, margin: "4px 0" };

  return (
    <Modal title="MCP server" open={open} onClose={onClose}>
      <label>
        <input type="checkbox" checked={on} onChange={(e) => put("mcp.enabled", e.target.checked ? "true" : "false")} /> <b>Enable MCP</b>{" "}
        <span className="muted">(AI clients can call the tools below while this is on)</span>
      </label>
      <div><span className={`dot ${on ? "ok" : ""}`} />{on ? "On" : "Off"}. Endpoint {url} (this computer only, token required)</div>
      <section aria-labelledby="h-quick" style={{ border: "1px solid var(--color-border)", borderRadius: 6, padding: 10 }}>
        <b id="h-quick">Quick start for Claude Code</b>
        <p style={{ margin: "4px 0" }}>One prompt does the whole setup. Press the button, open Claude Code, paste, and send. Claude connects itself, checks that it worked, and tells you what is on the bus.</p>
        <button className="btn primary" disabled={!qs} onClick={quick}>{on ? "Copy the setup prompt" : "Turn on MCP and copy the setup prompt"}</button>{" "}
        <button className="btn" onClick={() => setShowToken(!showToken)}>{showToken ? "Hide token" : "Show token"}</button>
        {qs && <pre className="mono" aria-label="Setup prompt" style={box}>{showToken ? qs.prompt : qs.prompt.split(token).join("********")}</pre>}
        <div className="muted">The prompt holds your private token, so only paste it into your own Claude. Sending frames stays off until you allow it below.</div>
      </section>
      <details>
        <summary><b>Other apps (Claude Desktop and similar)</b></summary>
        <p style={{ margin: "4px 0" }}>These start Busscript themselves, so no token is needed and this window does not have to be open. Add this to the app's MCP settings file:</p>
        <pre className="mono" style={box}>{stdioCfg}</pre>
        <button className="btn" onClick={() => copy(stdioCfg, "Copied the settings")}>Copy settings</button>
        <p className="muted" style={{ margin: "4px 0" }}>If a real adapter is connected, only one of Busscript and a started copy can use it at a time. Prefer the Claude Code prompt above in that case.</p>
      </details>
      <details>
        <summary><b>From a terminal</b></summary>
        <pre className="mono" style={box}>{`busscript mcp            # prints the same prompt
busscript mcp --setup    # turns MCP on and registers it with Claude Code
busscript status         # what Busscript is doing
busscript help           # everything the command line can do`}</pre>
      </details>
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
