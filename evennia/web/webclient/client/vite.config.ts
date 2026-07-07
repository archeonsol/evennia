import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { resolve } from "node:path";

// Build the default web client shell into Django's static tree as a single,
// self-executing bundle (shell.js + shell.css) that a template loads after
// evennia.js. Output to webclient/client2/ so it dual-routes alongside the
// legacy Golden Layout client until it reaches parity and becomes the default.
export default defineConfig({
  plugins: [svelte()],
  build: {
    outDir: resolve(__dirname, "../../static/webclient/client2"),
    emptyOutDir: true,
    cssCodeSplit: false,
    rollupOptions: {
      input: resolve(__dirname, "src/main.ts"),
      output: {
        format: "iife",
        entryFileNames: "shell.js",
        assetFileNames: "shell.[ext]",
        inlineDynamicImports: true,
      },
    },
  },
});
