import { pipeToHtml } from "./markup";

/** Convert the compose preview's plain Evennia-marked text to safe HTML. */
export function composePreviewToHtml(text: string): string {
  return pipeToHtml(text ?? "");
}
