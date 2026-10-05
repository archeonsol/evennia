// The Reading page: how the lines a player reads come out. The four settings
// live on the player's account in the game (the game's `@reading`), so they
// follow the player to every client. This file holds the shapes the game's
// typed requests carry and the few pure rules the page needs; the store that
// asks for them is `reading.svelte.ts`. Kept rune-free so it can be unit tested.

/** The settings, by the names the game gives them. */
export type ReadingSetting = "you" | "speech" | "aimed" | "spacing";

/** What `reading:reading_get` answers for the player's settings. */
export interface ReadingValues {
  /** Lines that name the player read "you". */
  you: boolean;
  /** A colour code such as "|r" for what people say, "" for none. */
  speech: string;
  speech_name: string;
  /** A colour code for lines aimed at the player, "" for none. */
  aimed_colour: string;
  aimed_colour_name: string;
  /** A marker before lines aimed at the player. */
  aimed_marker: boolean;
  /** A blank line before each post. */
  spacing: boolean;
}

/** One colour the game offers: a word, and the code it sends back. */
export interface ReadingColour {
  name: string;
  code: string;
}

/** What `reading:reading_get` and `reading:reading_set` answer. */
export interface ReadingAnswer {
  settings: ReadingValues;
  colours: ReadingColour[];
}

/**
 * The value to send for `aimed`: a colour, a marker, both, or `off`.
 *
 * The game takes them as words, as `@reading aimed red marker` does.
 */
export function aimedValue(colour: string, marker: boolean): string {
  const parts = [colour, marker ? "marker" : ""].filter(Boolean);
  return parts.length ? parts.join(" ") : "off";
}

/**
 * The colours to offer, with the player's current one among them.
 *
 * A colour set at the command line by its code (`@reading speech #ff8800`) is
 * not one of the offered words. Left out, the menu would show nothing chosen
 * and the next change would quietly replace it.
 */
export function coloursWith(
  colours: ReadingColour[],
  code: string,
  name: string,
): ReadingColour[] {
  if (!code || colours.some((c) => c.code === code)) return colours;
  return [...colours, { name: name || code.replace(/^\|/, ""), code }];
}

/** A colour word for a menu: its first letter in capitals. */
export function label(name: string): string {
  return name ? name[0].toUpperCase() + name.slice(1) : name;
}

/** `text` in a colour, as the game's markup. No colour leaves it plain. */
export function coloured(code: string, text: string): string {
  return code ? `${code}${text}|n` : text;
}

/** How a line aimed at the player reads under the settings: the page's sample. */
export function aimedSample(v: ReadingValues): string {
  const line = `${v.aimed_marker ? "» " : ""}Rook nods to you.`;
  return coloured(v.aimed_colour, line);
}

/** What a speaker says, as the page's sample shows it under the settings. */
export function speechSample(v: ReadingValues): string {
  return `Rook says, "${coloured(v.speech, "Quiet night.")}"`;
}
