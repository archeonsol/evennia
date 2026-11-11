// Compose-pad modes: the mapping between a mode, the command it sends, and the
// mode string the server previews it under. Rune-free so it can be
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
 * A pose that already names how it is sent: the "." and "," markers, and the
 * place poses "p." and "p," (`ppose`). Putting another "." in front of a place
 * pose sent it to the whole room as "Egret p.nods."
 */
const POSE_MARKER = /^(?:p?[.,])/i;

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
      return POSE_MARKER.test(t) ? t : `.${t}`;
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

/** The `compose_preview` request for a draft. */
export interface ComposePreviewRequest {
  mode: ComposeMode;
  text: string;
}

/** The preview request for a draft, or null if there is nothing to preview. */
export function composeToPreview(mode: ComposeMode, text: string): ComposePreviewRequest | null {
  let t = withBreaks(mode, text);
  // The preview shows the pose itself; a place pose's "p" marker is not part of it.
  if (mode === "pose" && /^p[.,]/i.test(t)) t = t.slice(1);
  return t ? { mode, text: t } : null;
}
