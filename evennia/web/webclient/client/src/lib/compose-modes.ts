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
 * A draft's line breaks, written the way the game reads them.
 *
 * A pose or emote may run over several lines, and the game takes `|/` as the
 * break between them. The pad's own line breaks become that here, so the
 * preview and the send read a draft the same way and nothing on the way treats
 * a newline as the end of a command. A say, LOOC or look line is one line, so
 * its breaks become spaces. Blank lines are dropped, and each line is trimmed.
 */
export function withBreaks(mode: ComposeMode, text: string): string {
  const lines = (text ?? "")
    .split(/\r\n?|\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  return lines.join(mode === "pose" || mode === "emote" ? "|/" : " ");
}

/**
 * The command line a composed draft actually sends.
 *
 * Pose uses the "." verb-marker shorthand rather than `pose <text>` because the
 * marker is significant to the emote parser, and the server's pose preview
 * strips exactly one leading "." to stay consistent with the send.
 */
export function composeToCommand(mode: ComposeMode, text: string): string {
  const t = withBreaks(mode, text);
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
  const t = withBreaks(mode, text);
  return t ? `@preview_rp ${mode} ${t}` : "";
}
