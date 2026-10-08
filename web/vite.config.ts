import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const target = process.env.LITLEDGER_DEV_API ?? "http://127.0.0.1:8765";

export default defineConfig({
  plugins: [react()],
  build: { outDir: "dist", chunkSizeWarningLimit: 1500 },
  server: {
    // "^/auth/" so the SPA's own /authorize route is not proxied.
    proxy: Object.fromEntries(["/api", "/events", "^/auth/", "/oauth", "/.well-known"].map((p) => [p, { target, ws: false }])),
  },
});
