import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  base: "/workspace/",
  plugins: [react()],
  server: {
    proxy: {
      "/api": "http://127.0.0.1:8766",
      "/paper.pdf": "http://127.0.0.1:8766",
      "/legacy": "http://127.0.0.1:8766"
    }
  }
});
