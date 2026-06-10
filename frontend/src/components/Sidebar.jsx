import { DashboardIcon, CheckboxIcon, CalendarIcon, LoopIcon, SunIcon, FileTextIcon, CommitIcon } from '@radix-ui/react-icons'
import './Sidebar.css'

const NAV_ITEMS = [
  { page: 'today',       label: 'Today',       Icon: SunIcon       },
  { page: 'overview',    label: 'Overview',    Icon: DashboardIcon },
  { page: 'tasks',       label: 'Tasks',       Icon: CheckboxIcon  },
  { page: 'calendar',    label: 'Calendar',    Icon: CalendarIcon  },
  { page: 'habits',      label: 'Habits',      Icon: LoopIcon      },
  { page: 'notes',       label: 'Notes',       Icon: FileTextIcon  },
  { page: 'engineering', label: 'Engineering', Icon: CommitIcon    },
]

export default function Sidebar({ tags, selectedTagId, page, onNavigate }) {
  const showTags = tags.length > 0 && page !== 'today'

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
