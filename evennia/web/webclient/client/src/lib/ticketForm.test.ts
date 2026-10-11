import { describe, expect, it } from "vitest";

import { DEFAULT_FORM, DEFAULT_PRIORITIES, mergeForm } from "./ticketForm";

describe("the words the form starts with", () => {
  it("offers the four kinds in the order a player expects", () => {
    expect(DEFAULT_FORM.kinds.map((k) => k.kind)).toEqual(["request", "bug", "report", "puppet"]);
  });

  it("calls the two fields a brief summary and details", () => {
    expect(DEFAULT_FORM.summary).toBe("Brief summary");
    expect(DEFAULT_FORM.details).toBe("Details");
  });

  it("gives every severity a meaning and a line saying it helps sort bugs", () => {
    expect(DEFAULT_FORM.severities.map((s) => s.key)).toEqual(["Trivial", "Minor", "Moderate", "Severe", "Critical"]);
    for (const s of DEFAULT_FORM.severities) expect(s.text.startsWith(`${s.key}: `)).toBe(true);
    expect(DEFAULT_FORM.severity_advice).toBe("It helps us sort bugs.");
  });

  it("tells a player staff answer as they can", () => {
    expect(DEFAULT_FORM.notes.request).toBe("Staff answer as they can.");
  });

  it("tells a player they may leave and wait, and asks for contact information", () => {
    expect(DEFAULT_FORM.notes.puppet).toContain("leave and wait");
    expect(DEFAULT_FORM.notes.puppet).toContain("contact information");
    expect(DEFAULT_FORM.contact).toBe("Contact information");
    expect(DEFAULT_FORM.kinds.find((k) => k.kind === "puppet")?.hint).toBe("After requesting, you can leave and go on with your RP while you wait.");
  });

  it("does not call lost items or money critical", () => {
    const critical = DEFAULT_FORM.severities.find((s) => s.key === "Critical")!;
    expect(critical.text).toBe("Critical: the game stops, or an exploit");
    expect(DEFAULT_PRIORITIES[3].hint).not.toContain("money");
  });

  it("names what each priority means to staff, and that only staff set the top one", () => {
    expect(DEFAULT_PRIORITIES.map((p) => p.word)).toEqual(["Low", "Normal", "High", "Urgent"]);
    expect(DEFAULT_PRIORITIES[3].hint).toContain("Only staff set this");
  });

  it("uses no word of the old labels", () => {
    const all = JSON.stringify([DEFAULT_FORM, DEFAULT_PRIORITIES]);
    expect(/needs you|waiting on|pending|resolved|urgency/i.test(all)).toBe(false);
    expect(all.includes("—")).toBe(false);
  });
});

describe("laying the game's words over them", () => {
  it("takes what the game says", () => {
    const form = mergeForm({ pick: "Hello?", summary: "Title", severity_advice: "Be honest." });
    expect(form.pick).toBe("Hello?");
    expect(form.summary).toBe("Title");
    expect(form.severity_advice).toBe("Be honest.");
  });

  it("keeps the default for a word the game leaves out or leaves empty", () => {
    const form = mergeForm({ pick: "", details: undefined } as any);
    expect(form.pick).toBe(DEFAULT_FORM.pick);
    expect(form.details).toBe("Details");
    expect(form.kinds).toEqual(DEFAULT_FORM.kinds);
  });

  it("takes the game's list of kinds and severities whole, in its order", () => {
    const form = mergeForm({
      kinds: [{ kind: "request", name: "Ask", hint: "Anything." }],
      severities: [{ key: "Minor", text: "Minor: small" }],
    });
    expect(form.kinds).toHaveLength(1);
    expect(form.severities).toEqual([{ key: "Minor", text: "Minor: small" }]);
  });

  it("carries no example text for a field", () => {
    expect((DEFAULT_FORM as unknown as Record<string, unknown>).placeholders).toBeUndefined();
  });

  it("fills a note the game did not write from the defaults", () => {
    const form = mergeForm({ notes: { report: "Private." } });
    expect(form.notes.report).toBe("Private.");
    expect(form.notes.puppet).toBe(DEFAULT_FORM.notes.puppet);
  });

  it("answers the defaults for no answer at all", () => {
    expect(mergeForm(null)).toBe(DEFAULT_FORM);
    expect(mergeForm(undefined)).toBe(DEFAULT_FORM);
  });
});
