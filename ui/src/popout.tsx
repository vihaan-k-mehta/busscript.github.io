import { useCallback, useEffect, useMemo, useState, type ReactNode } from "react";
import { api, type BusState, type FileSummary } from "./api";
import { live } from "./live";
import { DataPane, GraphicPane, StatPane, TracePane, WritePane, type LogLine } from "./panes";
import { FileInfoPane, FileTracePane } from "./filepane";
import { PaneContext, PANE_IDS, PANE_NAMES, type PaneControls, type PaneId } from "./layout";
import { closePopOut, lineChannel, usePopOutPresence, useShared } from "./dock";

export const popOutPane = (): PaneId | null => {
  const p = new URLSearchParams(location.search).get("pane");
  return PANE_IDS.includes(p as PaneId) ? (p as PaneId) : null;
};

/** One pane filling a window of its own. It shares the analyzer, the hex setting and the graph choices with the main window. */
export function PopOut({ pane }: { pane: PaneId }) {
  usePopOutPresence(pane);
  useEffect(() => { live.start(); document.title = `Busscript - ${PANE_NAMES[pane]}`; }, [pane]);
  const [hex] = useShared<boolean>("busscript-hex", true);
  const [plotted, setPlotted] = useShared<string[]>("busscript-plotted", []);
  const [info, setInfo] = useState<FileSummary | null>(null);
  const [running, setRunning] = useState(false);
  const [lines, setLines] = useState<LogLine[]>([]);

  useEffect(() => {
    const look = () => {
      api<FileSummary | null>("/api/file").then(setInfo).catch(() => {});
      api<BusState>("/api/state").then((s) => setRunning(!!s.running)).catch(() => {});
    };
    look();
    const t = window.setInterval(look, 1500);
    return () => window.clearInterval(t);
  }, []);
  useEffect(() => {
    const chan = lineChannel;
    if (!chan) return;
    const on = (e: MessageEvent) => setLines((l) => [...l.slice(-499), e.data as LogLine]);
    chan.addEventListener("message", on);
    return () => chan.removeEventListener("message", on);
  }, []);

  const onPlot = useCallback((name: string, on: boolean) =>
    setPlotted((p) => (on ? (p.includes(name) ? p : [...p, name]) : p.filter((n) => n !== name))), [setPlotted]);
  const fileKey = info ? `${info.name}|${info.frames}` : undefined;
  const controls: PaneControls = useMemo(() => ({
    hide: () => {}, swap: () => {}, nudge: () => {}, inOwnWindow: true, dockBack: () => closePopOut(pane),
  }), [pane]);
  const node: Record<PaneId, ReactNode> = {
    trace: info ? <FileTracePane info={info} hex={hex} rev={0} /> : <TracePane hex={hex} />,
    stat: info ? <FileInfoPane info={info} rev={0} /> : <StatPane />,
    data: <DataPane running={running} plotted={plotted} onPlot={onPlot} fileKey={fileKey} />,
    write: <WritePane lines={lines} />,
    graphic: <GraphicPane plotted={plotted} onPlot={onPlot} fileKey={fileKey} />,
  };
  return (
    <PaneContext.Provider value={controls}>
      <div className="popout">{node[pane]}</div>
    </PaneContext.Provider>
  );
}
