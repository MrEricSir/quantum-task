import './TagFilterBar.css'

export default function TagFilterBar({ tags, selectedTagId, page, onNavigate }) {
  if (!tags.length) return null

  return (
    <div className="tag-filter-bar">
      <button
        className={`tag-filter-bar-pill${selectedTagId === null ? ' tag-filter-bar-pill--active' : ''}`}
        onClick={() => onNavigate(page, null)}
      >
        All
      </button>
      {tags.map((tag) => (
        <button
          key={tag.id}
          className={`tag-filter-bar-pill${selectedTagId === tag.id ? ' tag-filter-bar-pill--active' : ''}`}
          style={
            selectedTagId === tag.id
              ? { background: tag.color, borderColor: tag.color, color: '#fff' }
              : { borderColor: tag.color, color: tag.color }
          }
          onClick={() => onNavigate(page, selectedTagId === tag.id ? null : tag.id)}
        >
          {tag.name}
        </button>
      ))}
    </div>
  )
}
