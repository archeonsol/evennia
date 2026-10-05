// Log lens categories and the type -> category mapping. Kept rune-free (like
// `scene-ops.ts` beside `scene.svelte.ts`) so it can be unit tested and so the
// session store can import it without pulling in reactive state.

export type LogCat = "speech" | "pose" | "combat" | "comms" | "ooc" | "look" | "system";

export const CATS: { id: LogCat; label: string }[] = [
  { id: "speech", label: "Speech" },
  { id: "pose", label: "Pose" },
  { id: "combat", label: "Combat" },
  { id: "comms", label: "Comms" },
  { id: "ooc", label: "OOC" },
  { id: "look", label: "Look" },
  { id: "system", label: "System" },
];

// A type is read as words, not as a string to search: "smell" is not "sm", and
// "channel_ooc" is the two words "channel" and "ooc".
//
// Out of character: LOOC, and every channel. A channel here is the game's chat
// between players, not something a character says; in-character radio arrives
// as comms.
const OOC = new Set(["looc", "ooc", "channel"]);
// What one person sent to another, or to a group: a page or tell, a text or
// group line on a handset, a broadcast, a Matrix (sm) line. Filing these under
// "system" meant hiding system text hid a message someone had sent you.
const COMMS = new Set(["comms", "page", "tell", "network", "sm", "handset", "broadcast"]);

// The vocabulary of types is a few dozen strings, and this runs once per line.
const seen = new Map<string, LogCat>();

function lens(type: string): LogCat {
  const t = type.toLowerCase();
  if (t === "say" || t === "whisper" || t === "speech") return "speech";
  if (t === "pose" || t === "emote") return "pose";
  if (t.includes("combat") || t.includes("damage")) return "combat";
  const words = t.split(/[^a-z0-9]+/);
  if (words.some((w) => OOC.has(w))) return "ooc";
  if (words.some((w) => COMMS.has(w))) return "comms";
  if (t === "look" || t === "room") return "look";
  return "system";
}

/** Bucket a server msg_type into the lens category its filter chip controls. */
export function categorize(type: string): LogCat {
  const key = type || "";
  let cat = seen.get(key);
  if (cat === undefined) {
    cat = lens(key);
    seen.set(key, cat);
  }
  return cat;
}
