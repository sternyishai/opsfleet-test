import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `/api` goes to `agent web`, so the browser sees one origin and no CORS setup is needed.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" } },
});
