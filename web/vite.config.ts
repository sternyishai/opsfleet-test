import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// `/api` goes to `agent web`, so the browser sees one origin and no CORS setup is needed.
// The navigator imports the diagrams and films from ../docs, so the dev server may read there.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://127.0.0.1:8000" }, fs: { allow: [".", "../docs"] } },
});
