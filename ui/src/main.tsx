import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { App } from "./App";
import { PopOut, popOutPane } from "./popout";

const pane = popOutPane();
createRoot(document.getElementById("root")!).render(<StrictMode>{pane ? <PopOut pane={pane} /> : <App />}</StrictMode>);
