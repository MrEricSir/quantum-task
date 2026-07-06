/**
 * Canonical section constants shared across the frontend.
 *
 * Section values (stored in DB and used throughout):
 *   today | week | month | later  — shown on the board
 *   none                          — reference card, shown only on the Cards page
 */

/** Board sections in display order (excludes 'none'). */
export const SECTIONS = ['today', 'week', 'month', 'later']

/** Human-readable labels for all sections including 'none'. */
export const SECTION_LABELS = {
  today: 'Today',
  week:  'This Week',
  month: 'This Month',
  later: 'Stash',
  none:  'Card',
}

/**
 * Full section list for form selects (includes 'none' for reference cards).
 * Each entry has `value` and `label`.
 */
export const ALL_SECTIONS = [
  { value: 'today', label: 'Today' },
  { value: 'week',  label: 'This Week' },
  { value: 'month', label: 'This Month' },
  { value: 'later', label: 'Stash' },
  { value: 'none',  label: 'Reference card' },
]
