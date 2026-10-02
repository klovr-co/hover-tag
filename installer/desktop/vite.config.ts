import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Tauri serves the built files; `npm run dev` also works in a browser with
// sample data (see src/lib/bridge.ts), which is how screenshots are made.
export default defineConfig({
  plugins: [react()],
  clearScreen: false,
  // Shared protocol examples and the brand icon live outside this folder.
  server: { port: 1420, strictPort: true, fs: { allow: ["../.."] } },
  envPrefix: ["VITE_", "TAURI_ENV_"],
  build: { target: "es2022", outDir: "dist" },
  test: { environment: "jsdom" },
});
