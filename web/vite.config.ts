import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// In dev, the orchestrator API runs on :8080 and Vite proxies /api to it.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: process.env.HARNESS_API ?? "http://localhost:8080", changeOrigin: true } },
  },
  test: { environment: "node" },
} as any);
