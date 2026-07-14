/**
 * Canonical section constants shared across the frontend.
 *
 * Section values (stored in DB and used throughout):
 *   today | week | month | later  — shown on the board
 */

/** Board sections in display order. */
export const SECTIONS = ['today', 'week', 'month', 'later']

/** Human-readable labels for all sections. */
export const SECTION_LABELS = {
  today: 'Today',
  week:  'This Week',
  month: 'This Month',
  later: 'Later',
}

/**
 * Full section list for form selects.
 * Each entry has `value` and `label`.
 */
export const ALL_SECTIONS = [
  { value: 'today', label: 'Today' },
  { value: 'week',  label: 'This Week' },
  { value: 'month', label: 'This Month' },
  { value: 'later', label: 'Later' },
]
