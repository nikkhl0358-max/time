import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [vue(), react()],
  server: {
    port: 5173,
    proxy: {
      "/api": {
        target: "http://127.0.0.1:5000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("xlsx-js-style")) return "vendor-xlsx";
          if (id.includes("lucide-react")) return "vendor-lucide";
        },
      },
    },
  },
});
