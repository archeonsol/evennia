import { defineConfig } from "vitest/config";
import { svelte } from "@sveltejs/vite-plugin-svelte";

// jsdom, not node. The defect this whole toolchain exists to catch was a
// renderer that threw the moment it ran, and a test environment without a DOM
// cannot run a renderer.
export default defineConfig({
  plugins: [svelte({ hot: false })],
  // Without this, Svelte resolves to its server build and `mount` throws
  // "not available on the server" -- so every component test fails for a
  // reason that has nothing to do with the component.
  resolve: { conditions: ["browser"] },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.ts"],
    setupFiles: ["src/test-setup.ts"],
  },
});
