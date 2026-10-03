import { Pause } from "lucide-react";
import { Field, Pane } from "./ui";

// #/design: every primitive in its states, for checking the design system in isolation.
export function DesignPage() {
  return (
    <div style={{ padding: 16, display: "grid", gap: 12, maxWidth: 900, margin: "0 auto" }}>
      <h1 style={{ margin: 0 }}>Design primitives</h1>
      <p className="muted">Tokens from replica/design/tokens.json. Each control is shown in its states.</p>
      <div style={{ height: 70 }}>
        <Pane title="Buttons and toolbar">
          <div style={{ padding: 8, display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button className="btn">Default</button>
            <button className="btn primary">Primary</button>
            <button className="btn danger">Danger</button>
            <button className="btn" disabled>Disabled</button>
            <button className="tb"><Pause size={14} /><span>Toolbar</span></button>
            <button className="tb" aria-pressed="true">Pressed</button>
            <button className="tb run">Start</button>
            <button className="tb stopbtn">Stop</button>
          </div>
        </Pane>
      </div>
      <div style={{ height: 110 }}>
        <Pane title="Inputs">
          <div style={{ padding: 8, display: "grid", gap: 8, gridTemplateColumns: "repeat(auto-fit,minmax(170px,1fr))" }}>
            <Field label="Text"><input type="text" defaultValue="0x123" /></Field>
            <Field label="Number"><input type="number" defaultValue={500000} /></Field>
            <Field label="Select"><select><option>Virtual bus</option></select></Field>
            <Field label="Error" error="Enter the ID as decimal or 0x hex."><input type="text" defaultValue="zz" aria-invalid="true" /></Field>
            <Field label="Disabled"><input type="text" disabled defaultValue="locked" /></Field>
          </div>
        </Pane>
      </div>
      <div style={{ height: 130 }}>
        <Pane title="Frame table rows">
          <div className="vtable" style={{ height: 95 }}>
            <div className="vhead"><span>Time [s]</span><span>Chn</span><span>Dir</span><span>ID</span><span>Name</span><span>DLC</span><span>Data</span></div>
            <div className="vrow" style={{ top: 20 }}><span className="mono">1.000000</span><span>can1</span><span>Rx</span><span className="mono">0x100</span><span>EngineData</span><span>8</span><span className="mono">40 1F 82 00</span></div>
            <div className="vrow tx" style={{ top: 37 }}><span className="mono">1.020000</span><span>can1</span><span>Tx</span><span className="mono">0x123</span><span>—</span><span>2</span><span className="mono">01 02</span></div>
            <div className="vrow err" style={{ top: 54 }}><span className="mono">1.030000</span><span>can1</span><span>ERR</span><span /><span>Error frame</span><span>0</span><span /></div>
            <div className="vrow sel" style={{ top: 71 }}><span className="mono">1.040000</span><span>can1</span><span>Rx</span><span className="mono">0x200</span><span>VehicleSpeed</span><span>4</span><span className="mono">E8 03</span></div>
          </div>
        </Pane>
      </div>
      <div style={{ height: 130 }}>
        <Pane title="Value bar, status, empty state">
          <div style={{ padding: 8, display: "grid", gap: 8 }}>
            <div className="bar" style={{ width: 200 }}><i style={{ width: "60%" }} /></div>
            <div><span className="dot ok" />OK <span className="dot warn" />Warning <span className="dot err" />Error</div>
            <div className="empty" style={{ padding: 0 }}>Empty state text</div>
          </div>
        </Pane>
      </div>
    </div>
  );
}
