import { defineConfig } from "vite";

const apiPaths = [
  "/health",
  "/status",
  "/arm",
  "/disarm",
  "/stop",
  "/drive",
  "/wheel",
  "/calibrate-sweep",
];

export default defineConfig({
  server: {
    port: 5173,
    proxy: Object.fromEntries(
      apiPaths.map((path) => [path, { target: "http://127.0.0.1:8080", changeOrigin: true }]),
    ),
  },
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
  },
});
