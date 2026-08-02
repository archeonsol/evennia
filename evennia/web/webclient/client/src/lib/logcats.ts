// Log lens categories and the type -> category mapping. Kept rune-free (like
// `scene-ops.ts` beside `scene.svelte.ts`) so it can be unit tested and so the
// session store can import it without pulling in reactive state.

export type LogCat = "speech" | "pose" | "combat" | "comms" | "look" | "system";

export const CATS: { id: LogCat; label: string }[] = [
  { id: "speech", label: "Speech" },
  { id: "pose", label: "Pose" },
  { id: "combat", label: "Combat" },
  { id: "comms", label: "Comms" },
  { id: "look", label: "Look" },
  { id: "system", label: "System" },
];

/** Bucket a server msg_type into the lens category its filter chip controls. */
export function categorize(type: string): LogCat {
  const t = (type || "").toLowerCase();
  if (t === "say" || t === "whisper" || t === "speech") return "speech";
  if (t === "pose" || t === "emote") return "pose";
  if (t.includes("combat") || t.includes("damage")) return "combat";
  if (["channel", "comms", "page", "tell", "network", "sm"].some((x) => t.includes(x)))
    return "comms";
  if (t === "look" || t === "room") return "look";
  return "system";
}
