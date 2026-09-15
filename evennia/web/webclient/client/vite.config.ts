import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { resolve } from "node:path";

// Build the default web client shell into Django's static tree as a single,
// self-executing bundle (shell.js + shell.css) that a game's webclient template
// loads. Output lands in webclient/shell/; it was webclient/client2/ while the
// shell dual-routed alongside the Golden Layout client, which it has replaced.
export default defineConfig({
  plugins: [svelte()],
  // Assets referenced from shell.css (the box-drawing fallback woff2) resolve
  // next to it. The default base is "/", which would point them at the site
  // root rather than STATIC_URL/webclient/shell/, and the font would 404.
  base: "./",
  build: {
    outDir: resolve(__dirname, "../../static/webclient/shell"),
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
