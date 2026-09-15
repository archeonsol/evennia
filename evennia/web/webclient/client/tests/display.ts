import { pipeToHtml } from "../src/lib/markup";
import { buildTranscript, htmlToAnsi } from "../src/lib/transcript";
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
  routing.process = (_html, text) => { seen.push(text); return false; };
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

// --- transcript export ---------------------------------------------------
const E = "\u001b[";
const ansiCases: [string, string][] = [
  ["plain", "plain"],
  ['<span class="color-009">red</span>', E + "38;5;9m" + "red" + E + "0m"],
  ['<span class="bgcolor-021">on blue</span>', E + "48;5;21m" + "on blue" + E + "0m"],
  [
    '<span class="underline blink color-011">loud</span>',
    E + "4;5;38;5;11m" + "loud" + E + "0m",
  ],
  [
    '<span class="color-009">red</span> bare',
    E + "38;5;9m" + "red" + E + "0m" + " bare",
  ],
  // Truecolor rides as an inline style and must win over the palette class.
  [
    '<span class="color-009" style="color: #102030;">tc</span>',
    E + "38;2;16;32;48m" + "tc" + E + "0m",
  ],
  // A link inherits the run it sits inside rather than resetting it.
  [
    '<span class="color-010"><a href="#">click</a></span>',
    E + "38;5;10m" + "click" + E + "0m",
  ],
  // Two runs in a row: the second must reset before restating, or the first
  // run's attributes accumulate onto it.
  [
    '<span class="color-009">a</span><span class="bgcolor-021">b</span>',
    E + "38;5;9m" + "a" + E + "0m" + E + "48;5;21m" + "b" + E + "0m",
  ],
  ['<span class="">a</span><br><span class="">b</span>', "a\nb"],
  ['<span class="">empty</span>', "empty"],
];
for (const [html, expected] of ansiCases) {
  test(`ansi ${html}`, () => equal(htmlToAnsi(html), expected));
}
test("ansi output opens a run without a leading reset and always closes it", () => {
  const out = htmlToAnsi('<span class="color-009">red</span>');
  equal(out.startsWith(E + "38;5;9m"), true);
  equal(out.endsWith(E + "0m"), true);
});
test("ansi output leaves unstyled text with no escapes at all", () => {
  equal(htmlToAnsi("<span class=\"\">bare</span> text"), "bare text");
});

const line = (html: string, text: string, ts = 0) => ({
  id: 0, html, text, type: "text", cat: "system" as const, ts,
});
test("txt export matches the plain-text projection", () => {
  const lines = [line('<span class="color-009">red</span>', "red"), line("plain", "plain")];
  const file = buildTranscript(lines as never, "txt");
  equal(file.body, "red\nplain");
  equal(file.ext, "txt");
});
test("html export keeps the colour class and drops the script", () => {
  const lines = [line('<span class="color-009">red</span><script>alert(1)</script>', "red")];
  const file = buildTranscript(lines as never, "html");
  equal(file.ext, "html");
  equal(file.body.includes('class="color-009"'), true);
  equal(file.body.includes("alert(1)"), false);
  // The palette is resolved from the live document, so the rule must be real.
  equal(/\.color-009\{color:[^}]+\}/.test(file.body), true);
});
test("html export strips inline handlers and javascript: urls", () => {
  const lines = [line('<a href="javascript:alert(1)" onclick="alert(2)">x</a>', "x")];
  const body = buildTranscript(lines as never, "html").body;
  equal(body.includes("onclick"), false);
  equal(body.includes("javascript:"), false);
  equal(body.includes(">x</a>"), true);
});
test("timestamps are opt-in, and the gutter carries no colour", () => {
  const lines = [line('<span class="color-009">red</span>', "red", Date.UTC(2020, 0, 1, 12, 0, 0))];
  equal(buildTranscript(lines as never, "txt").body, "red");
  const stamped = buildTranscript(lines as never, "txt", { timestamps: true }).body;
  equal(/^\d\d:\d\d:\d\d red$/.test(stamped), true);
  const ansi = buildTranscript(lines as never, "ansi", { timestamps: true }).body;
  equal(ansi.startsWith(E), false);
});


document.getElementById("results")!.textContent = results.join("\n");
document.title = `${results.filter((line) => line.startsWith("FAIL")).length} failures: Display regressions`;
