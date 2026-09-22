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
