import { CalendarIcon, SunIcon, TableIcon, CommitIcon, HeartIcon, CheckIcon } from '@radix-ui/react-icons'
import './Sidebar.css'

const NAV_ITEMS = [
  { page: 'today',       label: 'Today',       Icon: SunIcon      },
  { page: 'board',       label: 'Board',       Icon: TableIcon    },
  { page: 'calendar',    label: 'Calendar',    Icon: CalendarIcon },
  { page: 'health',      label: 'Habits',      Icon: HeartIcon    },
  { page: 'engineering', label: 'Engineering', Icon: CommitIcon   },
]

export default function Sidebar({ tags, selectedTagIds, page, onNavigate, onToggleTag, onClearTags }) {
  const showTags = tags.length > 0

  return (
    <aside className="sidebar">
      <nav className="sidebar-nav">
        {NAV_ITEMS.map(({ page: p, label, Icon }) => (
          <button
            key={p}
            className={`sidebar-item ${page === p ? 'sidebar-item--active' : ''}`}
            onClick={() => onNavigate(p)}
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
            <button
              className={`sidebar-item ${selectedTagIds.size === 0 ? 'sidebar-item--active' : ''}`}
              onClick={onClearTags}
            >
              All
            </button>
            {tags.map((tag) => {
              const active = selectedTagIds.has(tag.id)
              return (
                <button
                  key={tag.id}
                  className={`sidebar-item ${active ? 'sidebar-item--active' : ''}`}
                  onClick={() => onToggleTag(tag.id)}
                  aria-pressed={active}
                >
                  <span
                    className={`sidebar-tag-check${active ? ' sidebar-tag-check--on' : ''}`}
                    style={{ borderColor: tag.color, background: active ? tag.color : 'transparent' }}
                  >
                    {active && <CheckIcon width={9} height={9} />}
                  </span>
                  {tag.name}
                </button>
              )
            })}
          </nav>
        </>
      )}
    </aside>
  )
}
