import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CompletionLexicon } from "./lexicon.svelte";

function ok(names: string[]) {
  return async () => ({ names });
}

describe("completion lexicon", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  it("keeps only string verbs from the pushed lexicon", () => {
    const lex = new CompletionLexicon(async () => ({}));
    lex.setVerbs(["look", "get", 7, null, "!nod"]);
    expect(lex.verbs).toEqual(["look", "get", "!nod"]);
    lex.setVerbs(undefined);
    expect(lex.verbs).toEqual([]);
  });

  it("distinguishes never-fetched from fetched-empty", () => {
    const lex = new CompletionLexicon(async () => ({ names: [] }));
    expect(lex.scopeMatches("xyz")).toBeUndefined();
    return lex.fetchScope("xyz").then(() => {
      expect(lex.scopeMatches("xyz")).toEqual([]);
    });
  });

  it("asks the server once per word and answers from cache", async () => {
    const request = vi.fn(ok(["rusty key", "red keycard"]));
    const lex = new CompletionLexicon(request as any);

    expect(await lex.fetchScope("key")).toEqual(["rusty key", "red keycard"]);
    expect(await lex.fetchScope("key")).toEqual(["rusty key", "red keycard"]);
    expect(request).toHaveBeenCalledTimes(1);
    expect(request).toHaveBeenCalledWith("client", "complete", { word: "key" });
  });

  it("dedupes concurrent requests for one word", async () => {
    let resolveReq: (v: any) => void = () => {};
    const lex = new CompletionLexicon(
      () => new Promise((r) => (resolveReq = r)) as any,
    );
    const a = lex.fetchScope("bob");
    const b = lex.fetchScope("bob");
    resolveReq({ names: ["Bob"] });
    expect(await a).toEqual(["Bob"]);
    expect(await b).toEqual(["Bob"]);
  });

  it("expires cached scope answers and refetches", async () => {
    const request = vi.fn(ok(["rusty key"]));
    const lex = new CompletionLexicon(request as any);

    await lex.fetchScope("key");
    vi.advanceTimersByTime(6000);
    expect(lex.scopeMatches("key")).toBeUndefined();
    await lex.fetchScope("key");
    expect(request).toHaveBeenCalledTimes(2);
  });

  it("caches a failed request empty without poisoning the word", async () => {
    const request = vi
      .fn()
      .mockRejectedValueOnce(new Error("offline"))
      .mockResolvedValueOnce({ names: ["Bob"] });
    const lex = new CompletionLexicon(request as any);

    expect(await lex.fetchScope("bob")).toEqual([]);
    expect(await lex.fetchScope("bob")).toEqual([]);
    vi.advanceTimersByTime(6000);
    expect(await lex.fetchScope("bob")).toEqual(["Bob"]);
  });

  it("reset drops cached rooms' names", async () => {
    const lex = new CompletionLexicon(ok(["crate"]) as any);
    await lex.fetchScope("cr");
    lex.reset();
    expect(lex.scopeMatches("cr")).toBeUndefined();
  });
});
