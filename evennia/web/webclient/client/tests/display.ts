import { pipeToHtml } from "../src/lib/markup";
import { session } from "../src/lib/session.svelte";
import { triggers } from "../src/lib/triggers.svelte";
import { routing } from "../src/lib/routing.svelte";
import { markBacklog, typewriter } from "../src/lib/typewriter";
import parity from "../src/lib/__fixtures__/markup-parity.json";

// Open /tests/display.html on the Vite dev server. No game connection is used.
const results: string[] = [];
function equal(actual: unknown, expected: unknown): void {
  if (JSON.stringify(actual) !== JSON.stringify(expected)) {
    throw new Error(`Expected ${JSON.stringify(expected)}, received ${JSON.stringify(actual)}`);
  }
}
function test(name: string, check: () => void): void {
  try {
    check();
    results.push(`PASS ${name}`);
  } catch (error) {
    results.push(`FAIL ${name}: ${error}`);
  }
}

const textCases = [
  ["first<br>second", "first\nsecond"],
  ["<div>first</div><div>second</div>", "first\nsecond"],
  ["<section><h3>Title</h3><p>one</p><p>two</p></section>", "Title\none\ntwo"],
  ["<ul><li>one</li><li>two</li></ul>", "one\ntwo"],
  ["<p></p><p>x</p>", "x"],
  ["<p>x</p><p></p><p>y</p>", "x\ny"],
  ["<p>x</p><p></p>", "x"],
  ["<section><p></p></section>", ""],
  ["<div>one<br></div><div>two</div>", "one\ntwo"],
  ["<div>one</div><br><div>two</div>", "one\n\ntwo"],
  ["<br>one<br><br>", "\none\n\n"],
  ["<pre>  one\n\n two  </pre>", "  one\n\n two  "],
  ['<span title="a > b">R&amp;D &lt;x&gt; &amp;lt;x&amp;gt;</span>', "R&D <x> &lt;x&gt;"],
  ["a<style>hidden</style><script>hidden</script><template>hidden</template><span hidden>hidden</span>b", "ab"],
];
for (const [html, expected] of textCases) {
  test(`plain text ${html}`, () => {
    session.clear();
    session.append(html);
    equal(session.lines[0]?.text, expected);
    equal(session.transcript(), expected);
  });
}
test("downstream consumers share the same plain text", () => {
  const original = [triggers.shouldGag, triggers.runActions, routing.process] as const;
  const seen: string[] = [];
  triggers.shouldGag = (text) => { seen.push(text); return false; };
  triggers.runActions = (text) => { seen.push(text); };
  routing.process = (_html, text) => { seen.push(text); };
  try {
    session.clear();
    session.append("R&amp;D<br>next");
    equal(seen, ["R&D\nnext", "R&D\nnext", "R&D\nnext"]);
    equal(session.lines[0].text, "R&D\nnext");
  } finally {
    [triggers.shouldGag, triggers.runActions, routing.process] = original;
  }
});

const commands = ['say "hello"', 'say \\"hello"', 'look C:\\rooms\\', 'say <&> &quot;', 'say café 😀'];
for (const command of commands) {
  test(`client command roundtrip ${command}`, () => {
    const calls: unknown[] = [];
    Object.assign(window, { Evennia: { msg: (...args: unknown[]) => calls.push(args) } });
    const host = document.createElement("div");
    host.innerHTML = pipeToHtml(`|lc${command}|lt"click"|le`);
    host.querySelector("a")!.click();
    equal(calls, [["text", [command], {}]]);
    equal(host.textContent, '"click"');
  });
}
for (const entry of parity.filter((entry) => entry.src.startsWith("|lc") && entry.src.endsWith("|ltquoted|le"))) {
  test(`server command roundtrip ${entry.src}`, () => {
    const calls: unknown[] = [];
    Object.assign(window, { Evennia: { msg: (...args: unknown[]) => calls.push(args) } });
    const host = document.createElement("div");
    host.innerHTML = entry.html;
    host.querySelector("a")!.click();
    equal(calls, [["text", [entry.src.slice(3, -12)], {}]]);
  });
}

const request = window.requestAnimationFrame;
const cancel = window.cancelAnimationFrame;
const frames = new Map<number, FrameRequestCallback>();
let nextFrame = 0;
window.requestAnimationFrame = (callback) => { frames.set(++nextFrame, callback); return nextFrame; };
window.cancelAnimationFrame = (id) => { frames.delete(id); };
function tick(time: number): void {
  const pending = [...frames.values()];
  frames.clear();
  pending.forEach((callback) => callback(time));
}
markBacklog(0);
let nextLine = 0;
try {
  test("backlog and remounted lines stay complete", () => {
    const host = document.createElement("div");
    host.textContent = "complete";
    for (const id of [0, 0]) {
      const action = typewriter(host, { id, durationMs: 100 });
      equal(host.textContent, "complete");
      equal(frames.size, 0);
      action.destroy();
    }
    const id = ++nextLine;
    typewriter(host, { id, durationMs: 0 }).destroy();
    const remount = typewriter(host, { id, durationMs: 100 });
    equal(host.textContent, "complete");
    equal(frames.size, 0);
    remount.destroy();
  });
  test("nonzero timing updates affect future lines only", () => {
    const host = document.createElement("div");
    host.textContent = "abcd";
    const params = { id: ++nextLine, durationMs: 100 };
    const action = typewriter(host, params);
    try {
      tick(0);
      action.update({ ...params, durationMs: 1000 });
      tick(50);
      equal(host.textContent, "ab");
      tick(100);
      equal(host.textContent, "abcd");
      equal(frames.size, 0);
      action.update({ ...params, durationMs: 0 });
      equal(host.textContent, "abcd");
    } finally { action.destroy(); }
  });
  test("setting off restores active output and cancels its frame", () => {
    const host = document.createElement("div");
    host.innerHTML = "ab<span>cd</span>";
    const params = { id: ++nextLine, durationMs: 100 };
    const action = typewriter(host, params);
    try {
      tick(1);
      tick(26);
      equal(host.textContent, "a");
      action.update({ ...params, durationMs: 0 });
      equal(host.innerHTML, "ab<span>cd</span>");
      equal(frames.size, 0);
    } finally { action.destroy(); }
  });
  for (const html of ["A<span>😀</span>B", "A<span>e</span>\u0301B", "A<span>👨‍</span>👩‍👧‍👦B"]) {
    test(`whole graphemes across styled nodes ${html}`, () => {
      const host = document.createElement("div");
      host.innerHTML = html;
      const text = host.textContent!;
      const action = typewriter(host, { id: ++nextLine, durationMs: 100 });
      try {
        tick(1);
        tick(34);
        equal(host.textContent, "A");
        tick(51);
        equal(host.textContent, text.slice(0, -1));
        tick(101);
        equal(host.innerHTML, html);
        equal(frames.size, 0);
      } finally { action.destroy(); }
    });
  }
  test("without Segmenter output stays complete", () => {
    const descriptor = Object.getOwnPropertyDescriptor(Intl, "Segmenter")!;
    Object.defineProperty(Intl, "Segmenter", { value: undefined, configurable: true });
    try {
      const host = document.createElement("div");
      host.textContent = "😀text";
      const action = typewriter(host, { id: ++nextLine, durationMs: 100 });
      equal(host.textContent, "😀text");
      equal(frames.size, 0);
      action.destroy();
    } finally { Object.defineProperty(Intl, "Segmenter", descriptor); }
  });
} finally {
  window.requestAnimationFrame = request;
  window.cancelAnimationFrame = cancel;
}
document.getElementById("results")!.textContent = results.join("\n");
document.title = `${results.filter((line) => line.startsWith("FAIL")).length} failures: Display regressions`;
