// Runs the browser test pages (tests/*.html) in a real Chromium-family browser.
//
//   npm run test:browser
//
// Starts the Vite dev server for the client, opens each page, waits for it to
// report readiness, prints every PASS/FAIL line, and exits non-zero on failure.
// playwright-core is used deliberately: it drives a browser that is already
// installed (Edge or Chrome) and downloads nothing.

import { fileURLToPath } from "node:url";
import { createServer } from "vite";
import { chromium } from "playwright-core";

// Each page reports when it is done and where its PASS/FAIL lines live.
const PAGES = [
  {
    path: "tests/log.html",
    label: "virtualized log",
    done: () => window.__logTest?.done === true,
    report: () => window.__logTest ?? null,
  },
  {
    path: "tests/channel.html",
    label: "channel view",
    done: () => window.__channelTest?.done === true,
    report: () => window.__channelTest ?? null,
  },
  {
    path: "tests/notify.html",
    label: "tab alert",
    done: () => window.__notifyTest?.done === true,
    report: () => window.__notifyTest ?? null,
  },
  {
    path: "tests/workspace.html",
    label: "workspace panels",
    done: () => window.__workspaceTest?.done === true,
    report: () => window.__workspaceTest ?? null,
  },
  {
    path: "tests/display.html",
    label: "display regressions",
    done: () => /failures/.test(document.title),
    report: () => ({
      failures: Number.parseInt(document.title, 10),
      results: (document.getElementById("results")?.textContent ?? "").split("\n").filter(Boolean),
    }),
  },
];

async function launch() {
  const attempts = [
    // An explicit browser wins, e.g. a Playwright-managed Chromium in CI.
    ...(process.env.BROWSER_PATH ? [{ executablePath: process.env.BROWSER_PATH }] : []),
    { channel: "msedge" },
    { channel: "chrome" },
    { executablePath: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" },
    { executablePath: "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" },
    { executablePath: "/usr/bin/microsoft-edge" },
    { executablePath: "/usr/bin/google-chrome" },
  ];
  const errors = [];
  for (const attempt of attempts) {
    try {
      return await chromium.launch(attempt);
    } catch (error) {
      errors.push(`${JSON.stringify(attempt)}: ${error.message.split("\n")[0]}`);
    }
  }
  throw new Error(`no Chromium-family browser found:\n  ${errors.join("\n  ")}`);
}

const server = await createServer({
  configFile: fileURLToPath(new URL("../vite.config.ts", import.meta.url)),
  server: { host: "127.0.0.1", port: 5199, strictPort: false },
});
await server.listen();
const base = server.resolvedUrls?.local?.[0];
if (!base) throw new Error("vite did not report a local URL");

const browser = await launch();
let code = 0;
try {
  for (const spec of PAGES) {
    console.log(`\n== ${spec.label} (${spec.path}) ==`);
    const page = await browser.newPage();
    const consoleErrors = [];
    page.on("pageerror", (error) => consoleErrors.push(String(error)));
    page.on("console", (message) => {
      if (message.type() === "error") consoleErrors.push(message.text());
    });

    // "commit" only waits for the response; each page reports its own readiness.
    await page.goto(new URL(spec.path, base).href, { waitUntil: "commit", timeout: 60_000 });
    try {
      await page.waitForFunction(spec.done, null, { timeout: 90_000 });
    } catch {
      console.error(`timed out waiting for ${spec.path} to finish`);
      code = 1;
    }

    const report = await page.evaluate(spec.report);
    if (!report) {
      console.error("the page never reported results");
      code = 1;
    } else {
      for (const line of report.results) console.log(line);
      const failed = report.results.filter((line) => line.startsWith("FAIL"));
      console.log(`${report.results.length - failed.length} passed, ${failed.length} failed`);
      if (report.failures > 0) code = 1;
    }
    for (const error of consoleErrors) console.error(`console error: ${error}`);
    if (consoleErrors.length) code = 1;
    await page.close();
  }
} finally {
  await browser.close();
  await server.close();
}

process.exit(code);
