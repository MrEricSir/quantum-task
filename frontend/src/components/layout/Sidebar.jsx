import { CalendarIcon, SunIcon, TableIcon, CommitIcon, LightningBoltIcon, HeartIcon } from '@radix-ui/react-icons'
import './Sidebar.css'

const NAV_ITEMS = [
  { page: 'today',       label: 'Today',       Icon: SunIcon           },
  { page: 'board',       label: 'Board',       Icon: TableIcon         },
  { page: 'calendar',    label: 'Calendar',    Icon: CalendarIcon      },
  { page: 'health',      label: 'Habits',      Icon: HeartIcon         },
  { page: 'engineering', label: 'Engineering', Icon: CommitIcon        },
  { page: 'workshop',    label: 'Workshop',    Icon: LightningBoltIcon },
]

export default function Sidebar({ tags, selectedTagId, page, onNavigate }) {
  const showTags = tags.length > 0

  return (
    <aside className="sidebar">
      <nav className="sidebar-nav">
        {NAV_ITEMS.map(({ page: p, label, Icon }) => (
          <button
            key={p}
            className={`sidebar-item ${page === p ? 'sidebar-item--active' : ''}`}
            onClick={() => onNavigate(p, null)}
          >
            <span className="sidebar-item-icon"><Icon /></span>
            {label}
          </button>
        ))}
      </nav>

      {showTags && (
        <>
          <div className="sidebar-section-label">Tags</div>
          <nav className="sidebar-nav">
            {tags.map((tag) => (
              <button
                key={tag.id}
                className={`sidebar-item ${selectedTagId === tag.id ? 'sidebar-item--active' : ''}`}
                onClick={() => onNavigate(page, selectedTagId === tag.id ? null : tag.id)}
              >
                <span className="sidebar-dot" style={{ background: tag.color }} />
                {tag.name}
              </button>
            ))}
          </nav>
        </>
      )}
    </aside>
  )
}
