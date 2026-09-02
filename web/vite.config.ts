import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Built straight into the Python package so `uvicorn munshi.api:app` serves the
  // whole product from one process: one command to run the demo.
  build: { outDir: "../munshi/static", emptyOutDir: true },
  server: {
    proxy: { "/api": "http://127.0.0.1:8000", "/webhooks": "http://127.0.0.1:8000" },
  },
});
