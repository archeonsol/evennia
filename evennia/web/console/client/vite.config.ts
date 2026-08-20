import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { resolve } from "node:path";

// Build the console into Django's static tree as one self-contained module
// (console-app.js + console-app.css), which `templates/console/index.html`
// loads with a `{% static %}` tag.
//
// One file, not a split bundle. The plan asked for lazily loaded panels; the
// componentization is what that was for, and it is delivered in `src/panels/`.
// Splitting the *output* would make the browser resolve chunk URLs that a game
// is free to rewrite -- `ManifestStaticFilesStorage` hashes filenames and does
// not rewrite import specifiers inside JavaScript, so a split build breaks on
// exactly the deployments that hash their static files. Twenty-three panels of
// a staff tool are not worth that failure mode.
export default defineConfig({
  plugins: [svelte()],
  build: {
    outDir: resolve(__dirname, "../../static/console/app"),
    emptyOutDir: true,
    cssCodeSplit: false,
    rollupOptions: {
      input: resolve(__dirname, "src/main.ts"),
      output: {
        format: "es",
        entryFileNames: "console-app.js",
        assetFileNames: "console-app.[ext]",
        inlineDynamicImports: true,
      },
    },
  },
});
