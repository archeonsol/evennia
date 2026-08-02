// Compose-pad modes: the mapping between a mode, the command it sends, and the
// `@preview_rp` mode string the server previews it under. Rune-free so it can be
// unit tested (same split as `scene-ops.ts` / `logcats.ts`).
//
// The legacy client documented five modes but shipped one: its `setComposeMode`
// discarded its argument and hard-coded "pose", and `composeToCommand` always
// emitted the "." pose shorthand. The *server* has supported all five in
// `world/rpg/preview_rp.py` throughout, so this restores the intended behaviour
// rather than inventing it.

export type ComposeMode = "pose" | "emote" | "say" | "looc" | "lookplace";

export interface ComposeModeSpec {
  id: ComposeMode;
  label: string;
  /** msg_type class applied to the preview lines, matching the real output. */
  msgClass: string;
}

export const COMPOSE_MODES: ComposeModeSpec[] = [
  { id: "pose", label: "Pose", msgClass: "msg-pose" },
  { id: "emote", label: "Emote", msgClass: "msg-emote" },
  { id: "say", label: "Say", msgClass: "msg-say" },
  { id: "looc", label: "LOOC", msgClass: "msg-looc" },
  { id: "lookplace", label: "Look", msgClass: "msg-look" },
];

export function specFor(mode: ComposeMode): ComposeModeSpec {
  return COMPOSE_MODES.find((m) => m.id === mode) ?? COMPOSE_MODES[0];
}

/**
 * The command line a composed draft actually sends.
 *
 * Pose uses the "." verb-marker shorthand rather than `pose <text>` because the
 * marker is significant to the emote parser, and the server's pose preview
 * strips exactly one leading "." to stay consistent with the send.
 */
export function composeToCommand(mode: ComposeMode, text: string): string {
  const t = (text ?? "").trim();
  if (!t) return "";
  switch (mode) {
    case "pose":
      return t.startsWith(".") || t.startsWith(",") ? t : `.${t}`;
    case "emote":
      return `emote ${t}`;
    case "say":
      return `say ${t}`;
    case "looc":
      return `looc ${t}`;
    case "lookplace":
      return `@lp ${t}`;
  }
}

/** The `@preview_rp <mode> <text>` line for a draft, or "" if there is nothing to preview. */
export function composeToPreview(mode: ComposeMode, text: string): string {
  const t = (text ?? "").trim();
  return t ? `@preview_rp ${mode} ${t}` : "";
}
