import { defineConfig } from "vitest/config";
import { svelte } from "@sveltejs/vite-plugin-svelte";

export default defineConfig({
  // The svelte plugin is what compiles runes in `.svelte.ts` modules; without
  // it a test that constructs a `$state`-backed store dies on "$state is not
  // defined" at import time.
  plugins: [svelte()],
  test: {
    environment: "node",
    include: ["src/**/*.test.ts"],
    // Vitest stubs CSS imports by default, which swallows `?raw` too - and the
    // palette parity test reads ansi-palette.css that way.
    css: true,
  },
});
