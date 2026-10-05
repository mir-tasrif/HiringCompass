import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev server proxies /api to the local backend; in Docker, nginx does the same job.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/api": { target: "http://localhost:8000", changeOrigin: true, rewrite: (path) => path.replace(/^\/api/, "") },
    },
  },
});
