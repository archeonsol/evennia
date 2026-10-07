import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

// append() projects HTML to text through the DOM, which this environment lacks.
vi.mock("./text", () => ({ htmlToText: (html: string) => html.replace(/<[^>]*>/g, "") }));

import { compileLore, parseTips, wrapLore, type LoreTip } from "./lore";
import { lore } from "./lore.svelte";
import { session } from "./session.svelte";
import { settings } from "./settings.svelte";

function tip(id: string, terms: string[], more: Partial<LoreTip> = {}): LoreTip {
  return { id, title: id, blurb: "A note.", help: id, terms, ...more };
}

const marrow = tip("saint_marrow", ["Saint Marrow", "St. Marrow", "Marrow"], { mid: true });
const abzu = tip("abzu", ["Abzu"], { any_case: true });
const hum = tip("the_hum", ["the Hum", "The Hum"]);
const ferro = tip("saint_ferro", ["Saint Ferro", "Ferro"]);
const vesalius = tip("house_vesalius", ["House Vesalius", "Vesalius"]);

const matcher = compileLore([marrow, abzu, hum, ferro, vesalius]);
const wrap = (html: string) => wrapLore(html, matcher);
const lit = (id: string, text: string) => `<span class="lore" data-lore="${id}">${text}</span>`;
const plain = (html: string) => html.replace(/<[^>]*>/g, "");

describe("wrapLore", () => {
  it("marks a word where it stands", () => {
    expect(wrap("She prays to Marrow tonight.")).toBe(`She prays to ${lit("saint_marrow", "Marrow")} tonight.`);
  });

  it("marks every word in a line, whichever tip it belongs to", () => {
    expect(wrap("Abzu is older than Vesalius.")).toBe(
      `${lit("abzu", "Abzu")} is older than ${lit("house_vesalius", "Vesalius")}.`,
    );
  });

  it("takes the longest spelling that starts at the word", () => {
    expect(wrap("by Saint Marrow")).toBe(`by ${lit("saint_marrow", "Saint Marrow")}`);
    expect(wrap("on the shirt of St. Marrow's order")).toBe(
      `on the shirt of ${lit("saint_marrow", "St. Marrow")}'s order`,
    );
  });

  it("is exact about case unless the tip says any case", () => {
    expect(wrap("in the marrow of the bone")).toBe("in the marrow of the bone");
    expect(wrap("Abzu abzu ABZU")).toBe(`${lit("abzu", "Abzu")} ${lit("abzu", "abzu")} ${lit("abzu", "ABZU")}`);
  });

  it("takes a term written with the article either way", () => {
    expect(wrap("Listen to the Hum.")).toBe(`Listen to ${lit("the_hum", "the Hum")}.`);
    expect(wrap("The Hum is low.")).toBe(`${lit("the_hum", "The Hum")} is low.`);
    expect(wrap("the hum of a fan")).toBe("the hum of a fan");
  });

  it("needs a whole word, not a piece of one", () => {
    for (const line of ["Marrowind", "a Mythosis", "Abzus", "préAbzu", "Abzué"]) {
      expect(wrap(line)).toBe(line);
    }
  });

  it("does not take a word that starts a compound", () => {
    // A menu pun, not the Saint.
    expect(wrap("a Ferro-Fuel sandwich")).toBe("a Ferro-Fuel sandwich");
    expect(wrap("Ferro - the first smith")).toBe(`${lit("saint_ferro", "Ferro")} - the first smith`);
  });

  it("still takes a word with a possessive", () => {
    expect(wrap("at Marrow's tomb")).toBe(`at ${lit("saint_marrow", "Marrow")}'s tomb`);
    expect(wrap("at Marrow&#39;s tomb")).toBe(`at ${lit("saint_marrow", "Marrow")}&#39;s tomb`);
  });

  it("looks again inside a longer match that failed on case", () => {
    expect(wrap("by saint Marrow")).toBe(`by saint ${lit("saint_marrow", "Marrow")}`);
  });
});

describe("a word that is also plain English", () => {
  it("is left alone where it opens a sentence", () => {
    expect(wrap("Marrow help us.")).toBe("Marrow help us.");
    expect(wrap("It was cold. Marrow help us.")).toBe("It was cold. Marrow help us.");
    expect(wrap("Who? Marrow, of course.")).toBe("Who? Marrow, of course.");
    expect(wrap("&quot;Marrow keep you,&quot; she said.")).toBe("&quot;Marrow keep you,&quot; she said.");
  });

  it("lights up in the middle of one", () => {
    expect(wrap("She said, &quot;Marrow help us.&quot;")).toBe(
      `She said, &quot;${lit("saint_marrow", "Marrow")} help us.&quot;`,
    );
    expect(wrap("Pray to <span class=\"c1\">Marrow</span>.")).toBe(
      `Pray to <span class="c1">${lit("saint_marrow", "Marrow")}</span>.`,
    );
  });

  it("opens a sentence at the start of the line even when a colour tag comes first", () => {
    const line = '<span class="c1">Marrow</span> keeps the dead.';
    expect(wrap(line)).toBe(line);
  });

  it("opens a sentence after a line break", () => {
    expect(wrap("First line.<br>Marrow again")).toBe("First line.<br>Marrow again");
    expect(wrap("<p>One</p><p>Marrow</p>")).toBe("<p>One</p><p>Marrow</p>");
  });

  it("is not held back when its tip does not say so", () => {
    expect(wrap("Ferro was first.")).toBe(`${lit("saint_ferro", "Ferro")} was first.`);
  });

  it("is always matched by its qualified spelling", () => {
    expect(wrap("Saint Marrow keeps the dead.")).toBe(`${lit("saint_marrow", "Saint Marrow")} keeps the dead.`);
  });
});

describe("markup", () => {
  it("leaves tags and attributes alone", () => {
    const line = '<a href="/x" title="Marrow">pray</a>';
    expect(wrap(line)).toBe(line);
    expect(wrap('<a title="Marrow">to Marrow</a>')).toBe(`<a title="Marrow">to ${lit("saint_marrow", "Marrow")}</a>`);
  });

  it("is not fooled by an angle bracket inside a quoted attribute", () => {
    expect(wrap('<span data-x="a>Marrow">to Marrow</span>')).toBe(
      `<span data-x="a>Marrow">to ${lit("saint_marrow", "Marrow")}</span>`,
    );
  });

  it("keeps a stray angle bracket where it was", () => {
    expect(wrap("1 < 2 and Abzu")).toBe(`1 < 2 and ${lit("abzu", "Abzu")}`);
  });

  it("marks a word inside a colour span", () => {
    expect(wrap('<span class="ansi-red">Abzu</span>')).toBe(`<span class="ansi-red">${lit("abzu", "Abzu")}</span>`);
  });

  it("leaves script and style text alone", () => {
    expect(wrap("<style>Abzu { color: red }</style>to Abzu")).toBe(
      `<style>Abzu { color: red }</style>to ${lit("abzu", "Abzu")}`,
    );
  });

  it("does not change what the line says", () => {
    const line = 'To <b>Saint Marrow</b> &amp; Vesalius, in <span class="x">Abzu</span>&#39;s name.';
    expect(plain(wrap(line))).toBe(plain(line));
  });

  it("hands back the very same line when it holds no word", () => {
    const line = "Nothing to see in <b>here</b>.";
    expect(wrap(line)).toBe(line);
  });

  it("does nothing without a matcher", () => {
    expect(wrapLore("Marrow", null)).toBe("Marrow");
    expect(wrapLore("", matcher)).toBe("");
  });
});

describe("compileLore", () => {
  it("finds nothing to match in no tips", () => {
    expect(compileLore([])).toBeNull();
  });

  it("finds a tip again by its id", () => {
    expect(matcher?.byId.get("abzu")).toBe(abzu);
  });
});

describe("parseTips", () => {
  const good = { id: "abzu", title: "Abzu", blurb: "The Matrix.", help: "diving", terms: ["Abzu"] };

  it("takes a well formed reply", () => {
    expect(parseTips({ tips: [{ ...good, any_case: true, mid: true }] })).toEqual([
      { ...good, any_case: true, mid: true },
    ]);
  });

  it("carries a flag only when it is true", () => {
    expect(parseTips({ tips: [{ ...good, any_case: false, mid: "yes" }] })).toEqual([good]);
  });

  it("drops what is not well formed and keeps the rest", () => {
    const tips = [
      good,
      { ...good, id: "Bad Id" },
      { ...good, id: 'x"onmouseover="y' },
      { ...good, blurb: "" },
      { ...good, title: undefined },
      { ...good, terms: [] },
      { ...good, terms: "Abzu" },
      null,
      "text",
    ];
    expect(parseTips({ tips }).map((t) => t.id)).toEqual(["abzu"]);
  });

  it("makes nothing of a reply that is not a list of tips", () => {
    for (const reply of [null, undefined, "x", 3, {}, { tips: "x" }, []]) {
      expect(parseTips(reply)).toEqual([]);
    }
  });

  it("ignores empty or oversized terms", () => {
    expect(parseTips({ tips: [{ ...good, terms: ["", "Abzu", "x".repeat(81)] }] })[0].terms).toEqual(["Abzu"]);
  });
});

describe("the log", () => {
  beforeEach(() => {
    session.lines = [];
    session.archive = [];
    settings.screenreader = false;
    lore.setTips({ tips: [{ id: "abzu", title: "Abzu", blurb: "The Matrix.", help: "diving", terms: ["Abzu"] }] });
  });

  afterEach(() => {
    lore.setTips({ tips: [] });
    settings.screenreader = false;
  });

  it("marks a lore word as its line lands, and keeps the text plain", () => {
    session.append("She dives into Abzu.");

    expect(session.lines[0].html).toBe(`She dives into ${lit("abzu", "Abzu")}.`);
    expect(session.lines[0].text).toBe("She dives into Abzu.");
  });

  it("marks a line that a feed copies, too", () => {
    session.append("Abzu hums.");

    expect(session.archive[0].html).toContain('class="lore"');
  });

  it("leaves a media line and the echo of a typed command alone", () => {
    session.append("Abzu", "media");
    session.append("&gt; look Abzu", "echo");

    expect(session.lines.map((l) => l.html)).toEqual(["Abzu", "&gt; look Abzu"]);
  });

  it("marks nothing in screen reader mode", () => {
    settings.screenreader = true;
    session.append("She dives into Abzu.");

    expect(session.lines[0].html).toBe("She dives into Abzu.");
  });

  it("marks nothing once the game sends no words", () => {
    lore.setTips({ tips: [] });
    session.append("She dives into Abzu.");

    expect(lore.ready).toBe(false);
    expect(session.lines[0].html).toBe("She dives into Abzu.");
  });
});
