import { defineConfig } from "vite";
import { svelte } from "@sveltejs/vite-plugin-svelte";
import { resolve } from "node:path";

// The game server `npm run dev` talks to. Override with CONSOLE_API.
const CONSOLE_API = process.env.CONSOLE_API || "http://localhost:4001";

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
  server: {
    // `npm run dev` serves index.html from this directory. Two aliases make it
    // behave like the Django page without copying anything:
    //
    // `/engine-static/` reaches the engine's real stylesheets on disk, so the
    // development page and the served page cannot drift apart.
    //
    // `/api/console/` proxies to a running game server when there is one. When
    // there is not, requests fail and every panel renders its outage state --
    // which is a view worth being able to look at deliberately.
    fs: { allow: [resolve(__dirname, "../../static"), __dirname] },
    proxy: {
      "/api/console": {
        target: CONSOLE_API,
        changeOrigin: true,
        configure(proxy) {
          // Present the request as same-origin to the game server.
          //
          // The console's CSRF defence is an Origin check, and a dev server on
          // another port fails it. Rewriting the header here keeps that check
          // intact for real traffic while letting `npm run dev` work against an
          // unmodified server -- the alternative is asking every developer to
          // add a port to CSRF_TRUSTED_ORIGINS on a live game, which is a
          // durable weakening of a real defence for a temporary convenience.
          proxy.on("proxyReq", (request) => {
            request.setHeader("origin", CONSOLE_API);
            request.setHeader("referer", CONSOLE_API + "/console/");
          });
        },
      },
    },
  },
  resolve: {
    alias: { "/engine-static": resolve(__dirname, "../../static") },
  },
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
