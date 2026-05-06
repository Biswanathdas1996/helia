import { defineConfig, type UserConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";
import path from "path";
import runtimeErrorOverlay from "@replit/vite-plugin-runtime-error-modal";
import { mockupPreviewPlugin } from "./mockupPreviewPlugin";

const cartographerPlugins =
  process.env.NODE_ENV !== "production" && process.env.REPL_ID !== undefined
    ? [
        await import("@replit/vite-plugin-cartographer").then((m) =>
          m.cartographer({
            root: path.resolve(import.meta.dirname, ".."),
          }),
        ),
      ]
    : [];

export default defineConfig(({ command }): UserConfig => {
  const isServe = command === "serve";
  const rawPort = process.env.PORT;
  const basePath = process.env.BASE_PATH;

  if (isServe) {
    if (!rawPort) {
      throw new Error(
        "PORT environment variable is required but was not provided.",
      );
    }
    if (!basePath) {
      throw new Error(
        "BASE_PATH environment variable is required but was not provided.",
      );
    }
  }

  const port = rawPort ? Number(rawPort) : 0;
  if (rawPort && (Number.isNaN(port) || port <= 0)) {
    throw new Error(`Invalid PORT value: "${rawPort}"`);
  }

  return {
  base: basePath ?? "/",
  plugins: [
    mockupPreviewPlugin(),
    react(),
    tailwindcss(),
    runtimeErrorOverlay(),
    ...cartographerPlugins,
  ],
  resolve: {
    alias: {
      "@": path.resolve(import.meta.dirname, "src"),
    },
  },
  root: path.resolve(import.meta.dirname),
  build: {
    outDir: path.resolve(import.meta.dirname, "dist"),
    emptyOutDir: true,
  },
  server: {
    port,
    host: "0.0.0.0",
    allowedHosts: true,
    fs: {
      strict: true,
    },
  },
  preview: {
    port,
    host: "0.0.0.0",
    allowedHosts: true,
  },
  };
});
