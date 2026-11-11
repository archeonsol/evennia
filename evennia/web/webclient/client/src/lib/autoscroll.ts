/** Distance from the bottom that still counts as "reading the newest line". */
export const REPIN_PX = 24;
/**
 * Fractional layout and device-pixel rounding can leave a scroll event a hair
 * below the top we set ourselves; only a clearly lower top is the user.
 */
const USER_PX = 4;

/**
 * Pinned state after a scroll event.
 *
 * Growing content never lowers scrollTop, and a late-delivered event from our
 * own programmatic write reports exactly the top we set. So a top clearly below
 * that can only be the user leaving the bottom; every other event keeps the
 * current state. Reaching the bottom always re-pins.
 *
 * @param pinned Current pinned state.
 * @param gap Pixels between the visible bottom and the content bottom.
 * @param top The element's scrollTop at the event.
 * @param lastTop The scrollTop our code wrote most recently.
 * @returns The new pinned state.
 */
export function pinAfterScroll(pinned: boolean, gap: number, top: number, lastTop: number): boolean {
  if (gap <= REPIN_PX) return true;
  if (top < lastTop - USER_PX) return false;
  return pinned;
}

/**
 * Whether a search is holding its match in view. The log does not follow the
 * newest line while it is, so a line that lands is news the reader has not
 * seen, whether or not they have scrolled.
 *
 * @param open The search bar is open.
 * @param term What is typed in it.
 */
export function searchHolds(open: boolean, term: string): boolean {
  return open && term.trim() !== "";
}

/**
 * Whether the reader is away from the newest line, so arrivals are counted on
 * the "N new lines" bar. Scrolled up, or held in place by a search.
 *
 * @param pinned The log follows the newest line.
 * @param holding A search holds its match in view (see searchHolds).
 */
export function awayFromNewest(pinned: boolean, holding: boolean): boolean {
  return !pinned || holding;
}
