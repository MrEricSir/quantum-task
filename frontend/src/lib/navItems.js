/**
 * Canonical top-level nav pages, shared by Sidebar and MobileNav so the two
 * never drift the way they used to (each held its own hardcoded copy).
 *
 * Order here is only the built-in fallback -- the user's saved order (from
 * GET /api/settings/navigation) takes precedence wherever nav is rendered.
 */
import { CalendarIcon, SunIcon, TableIcon, CommitIcon, HeartIcon } from '@radix-ui/react-icons'

export const NAV_ITEMS = [
  { page: 'today',       label: 'Today',       Icon: SunIcon      },
  { page: 'board',       label: 'Board',       Icon: TableIcon    },
  { page: 'calendar',    label: 'Calendar',    Icon: CalendarIcon },
  { page: 'health',      label: 'Habits',      Icon: HeartIcon    },
  { page: 'engineering', label: 'Engineering', Icon: CommitIcon   },
]

export const NAV_PAGE_IDS = NAV_ITEMS.map((item) => item.page)

/**
 * Returns NAV_ITEMS reordered to match `order` (a list of page ids). Any id in
 * `order` that isn't a real page is ignored; any real page missing from `order`
 * (e.g. a saved order from before a page existed) is appended in default order.
 */
export function orderedNavItems(order) {
  if (!Array.isArray(order) || order.length === 0) return NAV_ITEMS
  const byPage = new Map(NAV_ITEMS.map((item) => [item.page, item]))
  const ordered = order.map((page) => byPage.get(page)).filter(Boolean)
  const seen = new Set(ordered.map((item) => item.page))
  for (const item of NAV_ITEMS) {
    if (!seen.has(item.page)) ordered.push(item)
  }
  return ordered
}
