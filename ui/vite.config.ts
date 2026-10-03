import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, the API and WebSocket live on the Python core.
const core = process.env.CORE_URL ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": core, "/ws": { target: core, ws: true } } },
});
