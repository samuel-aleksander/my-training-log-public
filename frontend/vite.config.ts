import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: 5173,
    host: true, // reachable from your phone on the same Wi-Fi
    // dev.sh exports BACKEND_PORT when it is changed from the default.
    proxy: { "/api": `http://127.0.0.1:${process.env.BACKEND_PORT ?? 8000}` },
  },
});
