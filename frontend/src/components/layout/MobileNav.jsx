import { orderedNavItems } from '../../lib/navItems'
import './MobileNav.css'

export default function MobileNav({ page, navOrder, onNavigate }) {
  const navItems = orderedNavItems(navOrder)
  return (
    <nav className="mobile-nav">
      {navItems.map(({ page: p, label, Icon }) => (
        <button
          key={p}
          className={`mobile-nav-item${page === p ? ' mobile-nav-item--active' : ''}`}
          onClick={() => onNavigate(p)}
        >
          <span className="mobile-nav-icon"><Icon width={16} height={16} /></span>
          <span className="mobile-nav-label">{label}</span>
        </button>
      ))}
    </nav>
  )
}
