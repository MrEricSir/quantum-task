import { SunIcon, CalendarIcon, LoopIcon, TableIcon, CommitIcon, LightningBoltIcon, HeartIcon } from '@radix-ui/react-icons'
import './MobileNav.css'

const NAV_ITEMS = [
  { page: 'today',       label: 'Today',       Icon: SunIcon           },
  { page: 'board',       label: 'Board',       Icon: TableIcon         },
  { page: 'calendar',    label: 'Calendar',    Icon: CalendarIcon      },
  { page: 'habits',      label: 'Habits',      Icon: LoopIcon          },
  { page: 'health',      label: 'Health',      Icon: HeartIcon         },
  { page: 'engineering', label: 'Engineering', Icon: CommitIcon        },
  { page: 'workshop',    label: 'Workshop',    Icon: LightningBoltIcon },
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
