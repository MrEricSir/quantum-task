import { CheckIcon } from '@radix-ui/react-icons'
import { orderedNavItems } from '../../lib/navItems'
import './Sidebar.css'

export default function Sidebar({ tags, selectedTagIds, page, navOrder, onNavigate, onToggleTag, onClearTags }) {
  const showTags = tags.length > 0
  const navItems = orderedNavItems(navOrder)

  return (
    <aside className="sidebar">
      <nav className="sidebar-nav">
        {navItems.map(({ page: p, label, Icon }) => (
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
