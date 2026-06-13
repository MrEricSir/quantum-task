import { SunIcon, DashboardIcon, CalendarIcon, LoopIcon, TableIcon, CommitIcon } from '@radix-ui/react-icons'
import './MobileNav.css'

const NAV_ITEMS = [
  { page: 'today',       label: 'Today',       Icon: SunIcon       },
  { page: 'overview',    label: 'Overview',    Icon: DashboardIcon },
  { page: 'board',       label: 'Board',       Icon: TableIcon     },
  { page: 'calendar',    label: 'Calendar',    Icon: CalendarIcon  },
  { page: 'habits',      label: 'Habits',      Icon: LoopIcon      },
  { page: 'engineering', label: 'Engineering', Icon: CommitIcon    },
]

export default function MobileNav({ page, onNavigate }) {
  return (
    <nav className="mobile-nav">
      {NAV_ITEMS.map(({ page: p, label, Icon }) => (
        <button
          key={p}
          className={`mobile-nav-item${page === p ? ' mobile-nav-item--active' : ''}`}
          onClick={() => onNavigate(p, null)}
        >
          <span className="mobile-nav-icon"><Icon width={16} height={16} /></span>
          <span className="mobile-nav-label">{label}</span>
        </button>
      ))}
    </nav>
  )
}
